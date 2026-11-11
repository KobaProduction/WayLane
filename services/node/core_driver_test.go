package main

import (
	"context"
	"io"
	"strings"
	"testing"

	"google.golang.org/protobuf/types/known/structpb"
)

type syntheticCoreDriver struct {
	called int
	coreID string
}

func (s *syntheticCoreDriver) ApplyDesired(_ context.Context, msg *controlEnvelope) (coreApplyResult, error) {
	s.called++
	s.coreID = msg.CoreID
	return coreApplyResult{Status: "running"}, nil
}

func TestControlDispatchesFutureCoreUsingSameBinaryEnvelope(t *testing.T) {
	const uuid = "11111111-1111-4111-8111-111111111111"
	payload, err := structpb.NewStruct(map[string]any{"configured": true, "generation": float64(8)})
	if err != nil {
		t.Fatal(err)
	}
	mock := &mockBinaryStream{inbox: []*controlEnvelope{
		{ProtocolVersion: 1, NodeUUID: uuid, MessageID: "ack-1", Kind: "hello_ack"},
		{ProtocolVersion: 1, NodeUUID: uuid, MessageID: "desired-1", Kind: "desired",
			CoreID: "awg/default", Generation: 8, Payload: payload},
	}}
	driver := &syntheticCoreDriver{}
	err = runControlStreamDrivers(context.Background(), mock, identity{UUID: uuid}, t.TempDir(),
		map[string]coreDriver{"awg/default": driver})
	if err != io.EOF {
		t.Fatalf("expected mock stream EOF, got %v", err)
	}
	if driver.called != 1 || driver.coreID != "awg/default" {
		t.Fatalf("wrong per-core dispatch: %+v", driver)
	}
	if len(mock.sent) < 3 || mock.sent[2].Kind != "result" || mock.sent[2].CoreID != "awg/default" ||
		mock.sent[2].Generation != 8 || mock.sent[2].Status != "running" {
		t.Fatalf("binary result was not routed to the incoming core: %+v", mock.sent)
	}
}

func TestControlRejectsUnregisteredAmneziaCore(t *testing.T) {
	const uuid = "11111111-1111-4111-8111-111111111111"
	mock := &mockBinaryStream{inbox: []*controlEnvelope{
		{ProtocolVersion: 1, NodeUUID: uuid, MessageID: "desired-1", Kind: "desired",
			CoreID: "awg/default", Generation: 1},
	}}
	err := runControlStreamDrivers(context.Background(), mock, identity{UUID: uuid}, t.TempDir(),
		map[string]coreDriver{"xray/default": xrayCoreDriver{socket: "/run/user/1000/docker.sock", stateDir: t.TempDir()}})
	if err == nil || !strings.Contains(err.Error(), "unknown core") {
		t.Fatalf("unimplemented AWG driver must fail closed: %v", err)
	}
	if len(mock.sent) != 1 || mock.sent[0].Kind != "hello" {
		t.Fatal("invalid core should never be acknowledged as applied")
	}
}
