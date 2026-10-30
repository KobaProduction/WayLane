package main

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/gorilla/websocket"
	"google.golang.org/protobuf/types/known/structpb"
	"math"
	"net/http"
	"net/url"
	"os"
	"sync"
	"time"
)

type binaryStream interface {
	Send(*controlEnvelope) error
	Recv() (*controlEnvelope, error)
	Close() error
}
type wsControlStream struct {
	ws      *websocket.Conn
	writeMu sync.Mutex
}

func (s *wsControlStream) Send(e *controlEnvelope) error {
	data, err := e.MarshalBinary()
	if err != nil {
		return err
	}
	s.writeMu.Lock()
	defer s.writeMu.Unlock()
	_ = s.ws.SetWriteDeadline(time.Now().Add(8 * time.Second))
	return s.ws.WriteMessage(websocket.BinaryMessage, data)
}
func (s *wsControlStream) Recv() (*controlEnvelope, error) {
	_ = s.ws.SetReadDeadline(time.Now().Add(55 * time.Second))
	kind, data, err := s.ws.ReadMessage()
	if err != nil {
		return nil, err
	}
	if kind != websocket.BinaryMessage {
		return nil, errors.New("text WS frame prohibited")
	}
	var msg controlEnvelope
	if err := msg.UnmarshalBinary(data); err != nil {
		return nil, err
	}
	return &msg, nil
}
func (s *wsControlStream) Close() error { return s.ws.Close() }

func newControlMessage(uuid, kind, core string, generation uint64, payload map[string]any) (*controlEnvelope, error) {
	random := make([]byte, 16)
	if _, err := rand.Read(random); err != nil {
		return nil, err
	}
	msg := &controlEnvelope{ProtocolVersion: 1, NodeUUID: uuid, MessageID: fmt.Sprintf("%x", random),
		Kind: kind, CoreID: core, Generation: generation}
	if payload != nil {
		p, err := structpb.NewStruct(payload)
		if err != nil {
			return nil, err
		}
		msg.Payload = p
	}
	return msg, nil
}
func wsControlConnect(ctx context.Context, origin string, ident identity) (binaryStream, error) {
	panel, err := url.Parse(origin)
	if err != nil {
		return nil, err
	}
	switch panel.Scheme {
	case "https":
		panel.Scheme = "wss"
	case "http":
		if os.Getenv("DEV_ALLOW_HTTP") != "true" {
			return nil, errors.New("insecure WS outside DEV")
		}
		panel.Scheme = "ws"
	default:
		return nil, errors.New("unrecognized URL scheme")
	}
	panel.Path = "/api/node-control/control/ws"
	panel.RawQuery = url.Values{"node_uuid": []string{ident.UUID}}.Encode()
	headers := http.Header{"X-Node-Secret": []string{ident.Secret}}
	dialer := websocket.Dialer{HandshakeTimeout: 8 * time.Second, Subprotocols: []string{"waylane.pb.v1"}}
	conn, resp, err := dialer.DialContext(ctx, panel.String(), headers)
	if err != nil {
		if resp != nil {
			return nil, fmt.Errorf("binary WS HTTP %d: %w", resp.StatusCode, err)
		}
		return nil, err
	}
	if conn.Subprotocol() != "waylane.pb.v1" {
		conn.Close()
		return nil, errors.New("binary WS protocol rejected")
	}
	conn.SetReadLimit(maxControlFrameBytes)
	return &wsControlStream{ws: conn}, nil
}

func normalizeControlNumbers(value any) any {
	switch item := value.(type) {
	case map[string]any:
		for key, nested := range item {
			item[key] = normalizeControlNumbers(nested)
		}
		return item
	case []any:
		for i, nested := range item {
			item[i] = normalizeControlNumbers(nested)
		}
		return item
	case float64:
		// Avoid float-to-int overflow and precision loss.
		if item >= -9007199254740991 && item <= 9007199254740991 && math.Trunc(item) == item {
			return int64(item)
		}
		return item
	default:
		return item
	}
}
func controlDesiredFromPayload(payload *structpb.Struct) (xrayDesired, error) {
	if payload == nil {
		return xrayDesired{}, errors.New("missing desired payload")
	}
	// Protobuf Struct numbers are doubles. Decode integral values as JSON
	// integers before passing them to existing typed Xray DTOs (generation,
	// TLS revision, listen_port), rather than producing 1.0 for an int field.
	raw, err := json.Marshal(normalizeControlNumbers(payload.AsMap()))
	if err != nil {
		return xrayDesired{}, err
	}
	var desired xrayDesired
	if err := json.Unmarshal(raw, &desired); err != nil {
		return xrayDesired{}, err
	}
	if !desired.Configured || desired.Generation < 1 || desired.Generation > 1<<53 {
		return xrayDesired{}, errors.New("invalid desired generation")
	}
	return desired, nil
}
func controlTelemetryPayload(state string) (map[string]any, error) {
	raw, err := json.Marshal(map[string]any{"node_version": version,
		"capabilities":    []string{"docker.rootless.inspect", "xray.protobuf.binary"},
		"machine_metrics": collectNodeRuntimeMetrics(state)})
	if err != nil {
		return nil, err
	}
	var result map[string]any
	err = json.Unmarshal(raw, &result)
	return result, err
}
func runControlStream(ctx context.Context, stream binaryStream, ident identity, state, socket string) error {
	return runControlStreamDrivers(ctx, stream, ident, state, map[string]coreDriver{
		"xray/default": xrayCoreDriver{socket: socket, stateDir: state},
	})
}

// Only registered core drivers may receive desired-state updates. This keeps
// future AmneziaWG management independent from the Xray state lifecycle.
func runControlStreamDrivers(ctx context.Context, stream binaryStream, ident identity, state string, drivers map[string]coreDriver) error {
	defer stream.Close()
	hello, err := newControlMessage(ident.UUID, "hello", "", 0, nil)
	if err != nil {
		return err
	}
	if err := stream.Send(hello); err != nil {
		return err
	}
	var sender sync.Mutex
	send := func(msg *controlEnvelope) error {
		sender.Lock()
		defer sender.Unlock()
		return stream.Send(msg)
	}
	sendTelemetry := func() error {
		payload, err := controlTelemetryPayload(state)
		if err != nil {
			return err
		}
		msg, err := newControlMessage(ident.UUID, "telemetry", "", 0, payload)
		if err != nil {
			return err
		}
		return send(msg)
	}
	stop := make(chan struct{})
	defer close(stop)
	go func() {
		ticker := time.NewTicker(15 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-stop:
				return
			case <-ctx.Done():
				return
			case <-ticker.C:
				if err := sendTelemetry(); err != nil {
					return
				}
				// Receive-side liveness: server replies with a binary pong
				// so idle connections do not hit the read deadline.
				ping, err := newControlMessage(ident.UUID, "ping", "", 0, nil)
				if err != nil || send(ping) != nil {
					return
				}
			}
		}
	}()
	for {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		reply, err := stream.Recv()
		if err != nil {
			return err
		}
		if reply.NodeUUID != ident.UUID || reply.ProtocolVersion != controlProtocolVersion {
			return errors.New("panel binary identity mismatch")
		}
		switch reply.Kind {
		case "hello_ack":
			if err := sendTelemetry(); err != nil {
				return err
			}
		case "ack", "pong":
		case "desired":
			if reply.Generation == 0 {
				return errors.New("binary core generation required")
			}
			driver, ok := drivers[reply.CoreID]
			if !ok || driver == nil {
				return errors.New("binary command targeted unknown core")
			}
			workCtx, cancel := context.WithTimeout(ctx, 4*time.Minute)
			outcome, err := driver.ApplyDesired(workCtx, reply)
			cancel()
			if err != nil {
				return err
			}
			result, err := newControlMessage(ident.UUID, "result", reply.CoreID, reply.Generation, nil)
			if err != nil {
				return err
			}
			result.Status = outcome.Status
			result.ErrorCode = outcome.ErrorCode
			if err := send(result); err != nil {
				return err
			}
		case "ping":
			pong, err := newControlMessage(ident.UUID, "pong", "", 0, nil)
			if err != nil {
				return err
			}
			if err := send(pong); err != nil {
				return err
			}
		default:
			return errors.New("unsupported panel binary control message")
		}
	}
}

// A single outbound binary WebSocket carries the entire Node management
// protocol. There is no transport switch, gRPC dial, or HTTP control fallback.
func runBinaryControl(ctx context.Context, origin string, ident identity, state, socket string) error {
	for {
		if ctx.Err() != nil {
			return nil
		}
		stream, err := wsControlConnect(ctx, origin, ident)
		if err == nil {
			err = runControlStream(ctx, stream, ident, state, socket)
		}
		if ctx.Err() != nil {
			return nil
		}
		// Bounded reconnect after network failure; never downgrade to text.
		_ = err
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(5 * time.Second):
		}
	}
}
