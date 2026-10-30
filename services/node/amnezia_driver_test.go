package main

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"testing"

	"google.golang.org/protobuf/types/known/structpb"
)

type mockAWGEngine struct {
	healthy                  int
	starts, reconfigs, stops int
	failure                  string
}

func (m *mockAWGEngine) Healthy(_ context.Context, _ awgWireConfig) error {
	m.healthy++
	if m.failure == "health" {
		return errors.New("synthetic no socket")
	}
	return nil
}

func (m *mockAWGEngine) Start(_ context.Context, _ awgWireConfig) error {
	m.starts++
	if m.failure == "start" {
		return errors.New("synthetic sensitive error")
	}
	return nil
}
func (m *mockAWGEngine) Reconfigure(_ context.Context, _, _ awgWireConfig) error {
	m.reconfigs++
	if m.failure == "requires_restart" {
		return errAWGRequiresRestart
	}
	if m.failure == "reconfigure" {
		return errors.New("synthetic sensitive error")
	}
	return nil
}
func (m *mockAWGEngine) Stop(_ context.Context, _ awgWireConfig) error {
	m.stops++
	if m.failure == "stop" {
		return errors.New("synthetic sensitive error")
	}
	return nil
}
func desiredAWGEnvelope(t *testing.T, generation int, action string, config awgWireConfig) *controlEnvelope {
	t.Helper()
	raw, err := json.Marshal(config)
	if err != nil {
		t.Fatal(err)
	}
	var data any
	if err := json.Unmarshal(raw, &data); err != nil {
		t.Fatal(err)
	}
	payload, err := structpb.NewStruct(map[string]any{"configured": true, "generation": float64(generation), "action": action, "config": data})
	if err != nil {
		t.Fatal(err)
	}
	return &controlEnvelope{ProtocolVersion: 1, NodeUUID: "11111111-1111-4111-8111-111111111111", MessageID: "qa-123", Kind: "desired", CoreID: "awg/default", Generation: uint64(generation), Operation: action, Payload: payload}
}
func TestAWGDriverPersistsIndependentGenerationAndDoesNotReplay(t *testing.T) {
	engine := &mockAWGEngine{}
	dir := t.TempDir()
	driver := awgCoreDriver{engine: engine, stateDir: dir}
	cfg := awgTestConfig()
	ctx := context.Background()
	first := desiredAWGEnvelope(t, 1, "deploy", cfg)
	res, err := driver.ApplyDesired(ctx, first)
	if err != nil || res.Status != "running" {
		t.Fatalf("deploy failed: %+v %v", res, err)
	}
	if _, err = driver.ApplyDesired(ctx, first); err != nil {
		t.Fatal(err)
	}
	if engine.starts != 1 {
		t.Fatal("applied generation replayed")
	}
	next := awgTestConfig()
	next.Peers[0].AllowedIPs = []string{"10.90.0.7/32"}
	res, err = driver.ApplyDesired(ctx, desiredAWGEnvelope(t, 2, "reconfigure", next))
	if err != nil || res.Status != "running" {
		t.Fatal(err, res)
	}
	if engine.reconfigs != 1 {
		t.Fatal("peer update missed")
	}
	state, err := readAWGState(dir)
	if err != nil {
		t.Fatal(err)
	}
	if state.AppliedGeneration != 2 || state.LastGeneration != 2 {
		t.Fatalf("wrong AWG generations: %+v", state)
	}
	path, err := awgStateFile(dir)
	if err != nil {
		t.Fatal(err)
	}
	file, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if file.Mode().Perm()&0077 != 0 {
		t.Fatal("private key state file readable by others")
	}
	res, err = driver.ApplyDesired(ctx, desiredAWGEnvelope(t, 3, "stop", next))
	if err != nil || res.Status != "stopped" || engine.stops != 1 {
		t.Fatal(err, res)
	}
}
func TestAWGDriverRetainsOldConfigAfterFailedPeerUpdate(t *testing.T) {
	engine := &mockAWGEngine{}
	dir := t.TempDir()
	driver := awgCoreDriver{engine: engine, stateDir: dir}
	ctx := context.Background()
	old := awgTestConfig()
	_, err := driver.ApplyDesired(ctx, desiredAWGEnvelope(t, 1, "deploy", old))
	if err != nil {
		t.Fatal(err)
	}
	engine.failure = "reconfigure"
	next := awgTestConfig()
	next.Peers[0].AllowedIPs = []string{"10.99.0.1/32"}
	desired := desiredAWGEnvelope(t, 2, "reconfigure", next)
	res, err := driver.ApplyDesired(ctx, desired)
	if err != nil || res.Status != "error" || res.ErrorCode != "awg_apply_failed" {
		t.Fatal(err, res)
	}
	again, err := driver.ApplyDesired(ctx, desired)
	if err != nil || again != res || engine.reconfigs != 1 {
		t.Fatal("failed generation was retried without new request")
	}
	state, err := readAWGState(dir)
	if err != nil {
		t.Fatal(err)
	}
	if state.Status != "running" || state.AppliedGeneration != 1 || state.LastGeneration != 2 {
		t.Fatalf("lost old running state: %+v", state)
	}
	var retained awgWireConfig
	if err := json.Unmarshal(state.Config, &retained); err != nil {
		t.Fatal(err)
	}
	if retained.Peers[0].AllowedIPs[0] != "10.90.0.2/32" {
		t.Fatal("failed reconfigure overwrote good peer list")
	}
	engine.failure = ""
	res, err = driver.ApplyDesired(ctx, desiredAWGEnvelope(t, 3, "reconfigure", next))
	if err != nil || res.Status != "running" {
		t.Fatal(err, res)
	}
}
func TestAWGDriverRejectsWrongCoreOrEnvelopeBeforeMutation(t *testing.T) {
	engine := &mockAWGEngine{}
	driver := awgCoreDriver{engine: engine, stateDir: t.TempDir()}
	ctx := context.Background()
	for _, edit := range []func(*controlEnvelope){
		func(m *controlEnvelope) { m.CoreID = "xray/default" },
		func(m *controlEnvelope) { m.Generation = 20 },
		func(m *controlEnvelope) { m.Operation = "restart" },
	} {
		msg := desiredAWGEnvelope(t, 1, "deploy", awgTestConfig())
		edit(msg)
		if _, err := driver.ApplyDesired(ctx, msg); err == nil {
			t.Fatal("invalid AWG envelope accepted")
		}
	}
	if engine.starts != 0 {
		t.Fatal("rejected envelope started engine")
	}
}

func TestAWGDriverReportsRestartRequiredWithoutChangingRunningConfig(t *testing.T) {
	engine := &mockAWGEngine{}
	dir := t.TempDir()
	driver := awgCoreDriver{engine: engine, stateDir: dir}
	ctx := context.Background()
	original := awgTestConfig()
	if _, err := driver.ApplyDesired(ctx, desiredAWGEnvelope(t, 1, "deploy", original)); err != nil {
		t.Fatal(err)
	}
	engine.failure = "requires_restart"
	changed := awgTestConfig()
	changed.Address = []string{"10.91.0.1/24"}
	result, err := driver.ApplyDesired(ctx, desiredAWGEnvelope(t, 2, "reconfigure", changed))
	if err != nil || result.Status != "requires_restart" || result.ErrorCode != "awg_requires_restart" {
		t.Fatal(err, result)
	}
	state, err := readAWGState(dir)
	if err != nil {
		t.Fatal(err)
	}
	if state.Status != "running" || state.AppliedGeneration != 1 || state.LastGeneration != 2 || engine.stops != 0 {
		t.Fatalf("live AWG state lost during rejected network update: %+v", state)
	}
}

func TestAWGDriverRefusesStaleRunningStatusIfChildUnhealthy(t *testing.T) {
	engine := &mockAWGEngine{}
	dir := t.TempDir()
	driver := awgCoreDriver{engine: engine, stateDir: dir}
	desired := desiredAWGEnvelope(t, 1, "deploy", awgTestConfig())
	if _, err := driver.ApplyDesired(context.Background(), desired); err != nil {
		t.Fatal(err)
	}
	engine.failure = "health"
	response, err := driver.ApplyDesired(context.Background(), desired)
	if err != nil || response.Status != "error" || response.ErrorCode != "awg_unhealthy" {
		t.Fatalf("unhealthy replay falsely reported running: %+v %v", response, err)
	}
	if engine.starts != 1 {
		t.Fatal("unhealthy replay started a new container")
	}
}
