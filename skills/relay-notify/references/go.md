# Relay client for Go apps

A single-file, standard-library client. Copy the code below into your app as
`internal/relay/relay.go` (any package path works; Go 1.22+), then:

```go
rc, err := relay.FromEnv() // RELAY_URL, RELAY_API_KEY, RELAY_APP, optional RELAY_USER
if err != nil {
	return err // missing config: ask the user for it, never guess
}

// At startup: is the key good and does the recipient exist and have a channel?
if err := rc.Check(ctx, "ali"); err != nil {
	return err // *relay.Error for a bad key; a plain error names the recipient problem
}

// Notify RELAY_USER (an error if it's unset: there is no default recipient)
id, err := rc.Notify(ctx, "low", "", "Nightly backup finished in 4m12s.")

// Anything richer: build the message from blocks (no templates)
id, err = rc.Send(ctx, relay.Message{
	To:      []string{"ali"},
	Urgency: "high",
	Title:   "Backup failed",
	Blocks: []relay.Block{
		{Type: "text", Text: "Nightly backup of nas stopped."},
		{Type: "fields", Items: []relay.Field{{Label: "Host", Value: "nas"}, {Label: "Error", Value: "disk full"}}},
		{Type: "code", Text: "rsync: write failed: No space left on device"},
	},
})

// Attach a file (one per message, up to 5 MB; 45 MB with a raised body limit), sent as a Telegram document
pdf, err := os.ReadFile("invoice.pdf")
if err != nil {
	return err
}
id, err = rc.Send(ctx, relay.Message{
	Title:  "September invoice",
	Blocks: []relay.Block{relay.FileBlock("invoice.pdf", "application/pdf", pdf, "Due Oct 15")},
})

// Ask with buttons and wait up to an hour
id, err = rc.Ask(ctx, "Ship v2.3 to production?", "Yes", "No")
wctx, cancel := context.WithTimeout(ctx, time.Hour)
defer cancel()
answer, err := rc.WaitForAnswer(wctx, id, 20*time.Second) // nil, nil: nobody answered in time
if err == nil && answer != nil && answer.Answer == "Yes" {
	// deploy
}

// Errors: *relay.Error carries Status, Code and Problems
var re *relay.Error
if errors.As(err, &re) && re.Code == "invalid_request" {
	log.Printf("relay rejected message: %v", re.Problems) // paths only, never your content
}
```

`Send` fills in `To` (`RELAY_USER`), `Source` (`RELAY_APP`) and a random
idempotency key when you leave them empty. With no `To` and no `RELAY_USER` it
returns `relay.ErrNoRecipient` without calling Relay: there is no default
recipient, so say who to notify. It and retries 5xx and network errors
with that same key, so a retry never sends twice. Pass your own
`IdempotencyKey` to make retries across restarts safe too. Never log
`Client.APIKey`.

```go
// Package relay is a small client for Relay (notify, ask, read answers).
// Copied from the relay-notify skill; standard library only.
package relay

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"time"
)

// Block is one content block; set only the fields its Type uses.
type Block struct {
	Type        string     `json:"type"`
	Text        string     `json:"text,omitempty"`
	Items       []Field    `json:"items,omitempty"`
	Columns     []string   `json:"columns,omitempty"`
	Rows        [][]string `json:"rows,omitempty"`
	URL         string     `json:"url,omitempty"`
	Base64      string     `json:"base64,omitempty"`
	ContentType string     `json:"content_type,omitempty"`
	Caption     string     `json:"caption,omitempty"`
	Filename    string     `json:"filename,omitempty"`
	Options     []string   `json:"options,omitempty"`
	Webhook     string     `json:"webhook,omitempty"`
	Reply       string     `json:"reply,omitempty"` // question: "voice" or "image" asks for a voice note or a picture
}

// ErrNoRecipient means a message had no To and RELAY_USER is unset.
var ErrNoRecipient = errors.New("relay: say who to notify: set Message.To or RELAY_USER")

// FileBlock attaches data as a file named filename (shown to the reader).
// contentType may be empty (application/octet-stream). Relay accepts one
// file per message, 1 byte to 5 MB (45 MB when the admin has raised
// RELAY_MAX_BODY_BYTES).
func FileBlock(filename, contentType string, data []byte, caption string) Block {
	return Block{
		Type: "file", Filename: filename, ContentType: contentType, Caption: caption,
		Base64: base64.StdEncoding.EncodeToString(data),
	}
}

// Field is one label/value line of a fields block.
type Field struct {
	Label string `json:"label"`
	Value string `json:"value"`
}

// Message is the body of POST /v1/messages. Empty To and Source are filled
// from RELAY_USER and RELAY_APP; an empty IdempotencyKey gets a random one.
type Message struct {
	To             []string `json:"to"`
	Urgency        string   `json:"urgency,omitempty"`
	Source         string   `json:"source,omitempty"`
	Title          string   `json:"title,omitempty"`
	Blocks         []Block  `json:"blocks"`
	IdempotencyKey string   `json:"idempotency_key,omitempty"`
}

// Recipient is someone Relay can notify. Addresses are never returned.
type Recipient struct {
	Username       string   `json:"username"`
	Aliases        []string `json:"aliases"`
	DisplayName    string   `json:"display_name"`
	LinkedChannels []string `json:"linked_channels"`
}

// Answer is one recipient's answer to a question.
type Answer struct {
	Recipient  string    `json:"recipient"`
	Answer     string    `json:"answer"` // empty for a voice note or picture
	Audio      *File     `json:"audio,omitempty"`
	Image      *File     `json:"image,omitempty"`
	AnsweredAt time.Time `json:"answered_at"`
}

// File is a voice-note or picture answer; Base64 decodes to its bytes.
type File struct {
	ContentType string `json:"content_type"`
	Base64      string `json:"base64"`
}

// Error is a Relay API error. Relay never echoes your content in it.
type Error struct {
	Status   int
	Code     string   `json:"code"`
	Message  string   `json:"message"`
	Problems []string `json:"problems"`
}

func (e *Error) Error() string {
	detail := e.Message
	if len(e.Problems) > 0 {
		detail = strings.Join(e.Problems, "; ")
	}
	return fmt.Sprintf("relay: %d %s: %s", e.Status, e.Code, detail)
}

// Client talks to one Relay. Never log or print APIKey.
type Client struct {
	URL, APIKey, User, App string
	HTTP                   *http.Client
}

// FromEnv reads RELAY_URL, RELAY_API_KEY, RELAY_APP and the optional
// RELAY_USER (who to notify when a message has no To; there is no default).
func FromEnv() (*Client, error) {
	c := &Client{
		URL:    strings.TrimRight(strings.TrimSpace(os.Getenv("RELAY_URL")), "/"),
		APIKey: strings.TrimSpace(os.Getenv("RELAY_API_KEY")),
		App:    strings.TrimSpace(os.Getenv("RELAY_APP")),
		User:   strings.TrimSpace(os.Getenv("RELAY_USER")),
		HTTP:   &http.Client{Timeout: 5 * time.Minute}, // room to upload a 45 MB file
	}
	if c.URL == "" || c.APIKey == "" || c.App == "" {
		return nil, errors.New("relay: set RELAY_URL, RELAY_API_KEY and RELAY_APP")
	}
	return c, nil
}

// Send posts m and returns the message ID. 5xx and network errors are
// retried with the same idempotency key, so nothing is sent twice.
func (c *Client) Send(ctx context.Context, m Message) (string, error) {
	if len(m.To) == 0 {
		if c.User == "" {
			return "", ErrNoRecipient
		}
		m.To = []string{c.User}
	}
	if m.Source == "" {
		m.Source = c.App
	}
	if m.IdempotencyKey == "" {
		b := make([]byte, 16)
		_, _ = rand.Read(b)
		m.IdempotencyKey = "auto-" + hex.EncodeToString(b)
	}
	var out struct{ ID string }
	err := c.do(ctx, http.MethodPost, "/v1/messages", m, &out)
	return out.ID, err
}

// Notify sends a text message to RELAY_USER.
func (c *Client) Notify(ctx context.Context, urgency, title, text string) (string, error) {
	return c.Send(ctx, Message{Urgency: urgency, Title: title, Blocks: []Block{{Type: "text", Text: text}}})
}

// Ask asks RELAY_USER a question: buttons with options, otherwise a typed reply.
func (c *Client) Ask(ctx context.Context, question string, options ...string) (string, error) {
	return c.Send(ctx, Message{Blocks: []Block{{Type: "question", Text: question, Options: options}}})
}

// Recipients lists who can be notified.
func (c *Client) Recipients(ctx context.Context) ([]Recipient, error) {
	var out struct{ Recipients []Recipient }
	err := c.do(ctx, http.MethodGet, "/v1/recipients", nil, &out)
	return out.Recipients, err
}

// Check sends nothing: it confirms the URL and key work and that each name
// (default RELAY_USER) is a recipient with a linked channel.
func (c *Client) Check(ctx context.Context, names ...string) error {
	if len(names) == 0 && c.User != "" {
		names = []string{c.User}
	}
	if len(names) == 0 {
		return ErrNoRecipient
	}
	rs, err := c.Recipients(ctx)
	if err != nil {
		return err
	}
	byName := map[string]Recipient{}
	for _, r := range rs {
		byName[r.Username] = r
		for _, a := range r.Aliases {
			byName[a] = r
		}
	}
	for _, n := range names {
		r, ok := byName[n]
		switch {
		case !ok:
			return fmt.Errorf("relay: %s: unknown recipient", n)
		case len(r.LinkedChannels) == 0:
			return fmt.Errorf("relay: %s: no linked channel yet, so nothing can reach them", n)
		}
	}
	return nil
}

// Answers returns the answers so far. Every answer returned becomes final.
func (c *Client) Answers(ctx context.Context, messageID string) ([]Answer, error) {
	var out struct{ Answers []Answer }
	err := c.do(ctx, http.MethodGet, "/v1/messages/"+messageID+"/answers", nil, &out)
	return out.Answers, err
}

// WaitForAnswer polls every interval until someone answers, and returns
// nil when ctx ends first (use context.WithTimeout as the deadline).
func (c *Client) WaitForAnswer(ctx context.Context, messageID string, every time.Duration) (*Answer, error) {
	for {
		as, err := c.Answers(ctx, messageID)
		if err != nil {
			if ctx.Err() != nil {
				return nil, nil
			}
			return nil, err
		}
		if len(as) > 0 {
			return &as[0], nil
		}
		select {
		case <-ctx.Done():
			return nil, nil
		case <-time.After(every):
		}
	}
}

func (c *Client) do(ctx context.Context, method, path string, in, out any) error {
	var body []byte
	if in != nil {
		var err error
		if body, err = json.Marshal(in); err != nil {
			return err
		}
	}
	var lastErr error
	for attempt := range 4 {
		if attempt > 0 {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(time.Duration(1<<(attempt-1)) * time.Second):
			}
		}
		req, err := http.NewRequestWithContext(ctx, method, c.URL+path, bytes.NewReader(body))
		if err != nil {
			return err
		}
		req.Header.Set("Authorization", "Bearer "+c.APIKey)
		req.Header.Set("Content-Type", "application/json")
		resp, err := c.HTTP.Do(req)
		if err != nil {
			lastErr = err
			continue
		}
		data, err := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
		_ = resp.Body.Close()
		if err != nil {
			lastErr = err
			continue
		}
		if resp.StatusCode >= 500 {
			lastErr = &Error{Status: resp.StatusCode, Code: "server_error"}
			continue
		}
		if resp.StatusCode >= 400 {
			var e struct{ Error Error }
			_ = json.Unmarshal(data, &e)
			e.Error.Status = resp.StatusCode
			return &e.Error
		}
		return json.Unmarshal(data, out)
	}
	return lastErr
}
```
