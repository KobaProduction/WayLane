package main

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"google.golang.org/protobuf/types/known/structpb"
	"io"
	"testing"
)

func TestControlEnvelopeBinaryProtobufRoundTrip(t *testing.T) {
	payload, err := structpb.NewStruct(map[string]any{
		"configured": true, "generation": float64(4),
		"config": map[string]any{"inbounds": []any{map[string]any{"tag": "socks-dev", "port": float64(18443)}}},
	})
	if err != nil {
		t.Fatal(err)
	}
	original := &controlEnvelope{ProtocolVersion: 1, NodeUUID: "11111111-1111-4111-8111-111111111111",
		MessageID: "qa-1", Kind: "desired", CoreID: "xray/default", Generation: 4,
		Operation: "reconfigure", Payload: payload}
	data, err := original.MarshalBinary()
	if err != nil || len(data) == 0 {
		t.Fatal(err)
	}
	if bytes.HasPrefix(data, []byte("{")) {
		t.Fatal("wire protocol is not binary protobuf")
	}
	var decoded controlEnvelope
	if err := decoded.UnmarshalBinary(data); err != nil {
		t.Fatal(err)
	}
	if decoded.ProtocolVersion != 1 || decoded.Generation != 4 || decoded.Kind != "desired" ||
		decoded.Payload.GetFields()["config"].GetStructValue() == nil {
		t.Fatalf("lost typed protobuf data: %+v", decoded)
	}
	codec := controlWireCodec{}
	encoded, err := codec.Marshal(original)
	if err != nil {
		t.Fatal(err)
	}
	var after controlEnvelope
	if err := codec.Unmarshal(encoded, &after); err != nil || after.NodeUUID != original.NodeUUID {
		t.Fatal("gRPC codec mismatch", err)
	}
}
func TestBinaryProtocolRejectsInvalidFrames(t *testing.T) {
	valid := &controlEnvelope{ProtocolVersion: 1, NodeUUID: "qa", MessageID: "id", Kind: "hello"}
	raw, err := valid.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	for _, candidate := range [][]byte{nil, {}, []byte("{}"), bytes.Repeat([]byte{0x20}, maxControlFrameBytes+1)} {
		var msg controlEnvelope
		if err := msg.UnmarshalBinary(candidate); err == nil {
			t.Fatalf("invalid frame accepted len=%d", len(candidate))
		}
	}
	valid.ProtocolVersion = 2
	if _, err := valid.MarshalBinary(); err == nil {
		t.Fatal("unsupported protocol version")
	}
	var msg controlEnvelope
	if err := msg.UnmarshalBinary(raw); err != nil {
		t.Fatal(err)
	}
}

func TestPythonGeneratedProtoWireCompatibility(t *testing.T) {
	// Generated with Python grpc_tools against shared/contracts/proto.
	data, err := hex.DecodeString("0801122431313131313131312d313131312d343131312d383131312d3131313131313131313131311a0471612d312207646573697265642a0c787261792f64656661756c7430043a0b7265636f6e66696775726542740a100a0a636f6e66696775726564120220010a470a06636f6e666967123d2a3b0a390a08696e626f756e6473122d322b0a292a270a110a04706f727412091100000000c002d2400a120a03746167120b1a09736f636b732d6465760a170a0a67656e65726174696f6e1209110000000000001040")
	if err != nil {
		t.Fatal(err)
	}
	var msg controlEnvelope
	if err := msg.UnmarshalBinary(data); err != nil {
		t.Fatal(err)
	}
	if msg.Kind != "desired" || msg.NodeUUID != "11111111-1111-4111-8111-111111111111" ||
		msg.Generation != 4 || msg.Operation != "reconfigure" || msg.Payload == nil {
		t.Fatalf("cross-language Protobuf decode mismatch: %+v", msg)
	}
	desired, decodeErr := controlDesiredFromPayload(msg.Payload)
	if decodeErr != nil || desired.Generation != 4 {
		t.Fatalf("nested protobuf numeric generation decode: %+v %v", desired, decodeErr)
	}
	if !msg.Payload.GetFields()["configured"].GetBoolValue() {
		t.Fatal("Python Protobuf Struct nested payload not readable in Go")
	}
	converted, err := msg.MarshalBinary()
	if err != nil {
		t.Fatal(err)
	}
	var check controlEnvelope
	if err := check.UnmarshalBinary(converted); err != nil || check.Payload == nil {
		t.Fatal("Go cannot re-encode Python Protobuf envelope", err)
	}
}

type mockBinaryStream struct {
	sent  []*controlEnvelope
	inbox []*controlEnvelope
}

func (m *mockBinaryStream) Send(v *controlEnvelope) error { m.sent = append(m.sent, v); return nil }
func (m *mockBinaryStream) Recv() (*controlEnvelope, error) {
	if len(m.inbox) == 0 {
		return nil, io.EOF
	}
	next := m.inbox[0]
	m.inbox = m.inbox[1:]
	return next, nil
}
func (m *mockBinaryStream) Close() error { return nil }

func TestAgentBinaryControlHandshakeAndPersistedResult(t *testing.T) {
	uuid := "11111111-1111-4111-8111-111111111111"
	state := t.TempDir()
	saved := xrayLocalState{Generation: 4, Status: "running", ImageVersion: "26.10.21",
		Config: json.RawMessage("{\"inbounds\":[{\"tag\":\"a\"}],\"outbounds\":[{\"tag\":\"d\"}]}")}
	if err := saveXrayState(state, saved); err != nil {
		t.Fatal(err)
	}
	desiredStruct, err := structpb.NewStruct(map[string]any{
		"configured": true, "generation": float64(4), "image_version": "26.10.21",
		"action": "reconfigure",
	})
	if err != nil {
		t.Fatal(err)
	}
	mock := &mockBinaryStream{inbox: []*controlEnvelope{
		{ProtocolVersion: 1, NodeUUID: uuid, MessageID: "from-server-1", Kind: "hello_ack"},
		{ProtocolVersion: 1, NodeUUID: uuid, MessageID: "from-server-2", Kind: "desired",
			CoreID: "xray/default", Generation: 4, Payload: desiredStruct},
	}}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	err = runControlStream(ctx, mock, identity{UUID: uuid, Registered: true}, state, "/run/user/1000/docker.sock")
	if err != io.EOF {
		t.Fatalf("mock must close cleanly at EOF: %v", err)
	}
	if len(mock.sent) < 3 || mock.sent[0].Kind != "hello" ||
		mock.sent[1].Kind != "telemetry" || mock.sent[2].Kind != "result" ||
		mock.sent[2].Generation != 4 || mock.sent[2].Status != "running" {
		t.Fatalf("binary Agent control did not exchange hello, telemetry and result")
	}
}
