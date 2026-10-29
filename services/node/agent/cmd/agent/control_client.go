package main

import (
	"context"
	"crypto/rand"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"github.com/gorilla/websocket"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/protobuf/types/known/structpb"
	"math"
	"net"
	"net/http"
	"net/url"
	"os"
	"strings"
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

type grpcControlStream struct {
	stream grpc.ClientStream
	conn   *grpc.ClientConn
}

func (s *grpcControlStream) Send(e *controlEnvelope) error { return s.stream.SendMsg(e) }
func (s *grpcControlStream) Recv() (*controlEnvelope, error) {
	var e controlEnvelope
	if err := s.stream.RecvMsg(&e); err != nil {
		return nil, err
	}
	return &e, nil
}
func (s *grpcControlStream) Close() error { _ = s.stream.CloseSend(); return s.conn.Close() }

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
		if os.Getenv("WAYLANE_DEV_ALLOW_HTTP") != "true" {
			return nil, errors.New("insecure WS outside DEV")
		}
		panel.Scheme = "ws"
	default:
		return nil, errors.New("unrecognized URL scheme")
	}
	panel.Path = "/api/agent/control/ws"
	panel.RawQuery = url.Values{"node_uuid": []string{ident.UUID}}.Encode()
	headers := http.Header{"X-Agent-Secret": []string{ident.Secret}}
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
func grpcControlConnect(ctx context.Context, ident identity) (binaryStream, error) {
	target := os.Getenv("WAYLANE_CONTROL_GRPC_TARGET")
	if target == "" || strings.Contains(target, "://") || strings.Contains(target, "/") {
		return nil, errors.New("gRPC target must be host:port")
	}
	host, _, err := net.SplitHostPort(target)
	if err != nil {
		return nil, err
	}
	opts := []grpc.DialOption{grpc.WithDefaultCallOptions(grpc.ForceCodec(controlWireCodec{})), grpc.WithBlock()}
	if os.Getenv("WAYLANE_CONTROL_GRPC_DEV_INSECURE") == "true" {
		if os.Getenv("WAYLANE_DEV_ALLOW_HTTP") != "true" || (host != "host.docker.internal" && net.ParseIP(host) == nil) {
			return nil, errors.New("plaintext gRPC forbidden")
		}
		opts = append(opts, grpc.WithTransportCredentials(insecure.NewCredentials()))
	} else {
		opts = append(opts, grpc.WithTransportCredentials(credentials.NewTLS(&tls.Config{MinVersion: tls.VersionTLS12})))
	}
	dialCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	conn, err := grpc.DialContext(dialCtx, target, opts...)
	if err != nil {
		return nil, err
	}
	streamCtx := metadata.AppendToOutgoingContext(ctx, "x-node-uuid", ident.UUID, "x-agent-secret", ident.Secret)
	stream, err := conn.NewStream(streamCtx, &grpc.StreamDesc{ServerStreams: true, ClientStreams: true},
		"/waylane.control.v1.AgentControl/Connect")
	if err != nil {
		conn.Close()
		return nil, err
	}
	return &grpcControlStream{stream: stream, conn: conn}, nil
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
	raw, err := json.Marshal(map[string]any{"agent_version": version,
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
				_ = sendTelemetry()
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
			if reply.CoreID != "xray/default" || reply.Generation == 0 {
				return errors.New("binary command targeted unknown core")
			}
			desired, err := controlDesiredFromPayload(reply.Payload)
			if err != nil {
				return err
			}
			if uint64(desired.Generation) != reply.Generation {
				return errors.New("desired generation mismatch")
			}
			local, err := readXrayState(state)
			if err != nil {
				return err
			}
			if desired.Generation > local.Generation {
				workCtx, cancel := context.WithTimeout(ctx, 4*time.Minute)
				updated, applyErr := applyXrayDesired(workCtx, socket, state, local, desired)
				cancel()
				if applyErr != nil {
					updated.Status = "error"
					if errors.Is(applyErr, errLiveRequiresRestart) {
						updated.Status = "requires_restart"
					}
					updated.Error = applyErr.Error()
					if len(updated.Error) > 240 {
						updated.Error = updated.Error[:240]
					}
				}
				if err := saveXrayState(state, updated); err != nil {
					return err
				}
				local = updated
			}
			result, err := newControlMessage(ident.UUID, "result", "xray/default", uint64(desired.Generation), nil)
			if err != nil {
				return err
			}
			result.Status = local.Status
			if local.Error != "" {
				result.ErrorCode = "xray_apply_failed"
			}
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

func runBinaryControl(ctx context.Context, origin string, ident identity, state, socket, transport string) error {
	for {
		if ctx.Err() != nil {
			return nil
		}
		var stream binaryStream
		var err error
		switch transport {
		case "ws":
			stream, err = wsControlConnect(ctx, origin, ident)
		case "grpc":
			stream, err = grpcControlConnect(ctx, ident)
		default:
			return errors.New("unsupported binary transport")
		}
		if err == nil {
			err = runControlStream(ctx, stream, ident, state, socket)
		}
		if ctx.Err() != nil {
			return nil
		}
		// No fallback to HTTP or text transports; bounded reconnection.
		_ = err
		select {
		case <-ctx.Done():
			return nil
		case <-time.After(5 * time.Second):
		}
	}
}
