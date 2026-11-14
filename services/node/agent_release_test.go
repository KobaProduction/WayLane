package main

import (
	"context"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"testing"
	"time"
)

const fixtureReleaseID = "0192b532-184f-41bc-9952-78e15b14cc55"
const fixtureReleaseImage = "ghcr.io/kobaproduction/waylane-node@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

func TestNodePrivateAgentUnixUpdateStatusAndWSSReady(t *testing.T) {
	original := agentSocketPath
	agentSocketPath = filepath.Join(t.TempDir(), "agent.sock")
	t.Cleanup(func() { agentSocketPath = original })
	listener, err := net.Listen("unix", agentSocketPath)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	if err := os.Chmod(agentSocketPath, 0600); err != nil {
		t.Fatal(err)
	}
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method + " " + r.URL.Path {
		case "GET /v1/node":
			json.NewEncoder(w).Encode(map[string]any{"api_version": 1, "image": "waylane-node-seed:bootstrap"})
		case "GET /v1/node/release":
			json.NewEncoder(w).Encode(agentReleaseStatus{APIVersion: 1, OperationID: fixtureReleaseID,
				Image: fixtureReleaseImage, PreviousImage: "waylane-node-seed:bootstrap",
				Status: "running", Phase: "awaiting_wss", Progress: 85})
		case "PUT /v1/node/release":
			var v struct {
				APIVersion  int    `json:"api_version"`
				OperationID string `json:"operation_id"`
				Image       string `json:"image"`
			}
			if json.NewDecoder(r.Body).Decode(&v) != nil || v.APIVersion != 1 || v.OperationID != fixtureReleaseID || v.Image != fixtureReleaseImage {
				t.Error("invalid private release payload")
			}
			w.WriteHeader(http.StatusAccepted)
			json.NewEncoder(w).Encode(agentReleaseStatus{APIVersion: 1, OperationID: v.OperationID, Image: v.Image, Status: "queued", Phase: "queued", Progress: 0})
		case "POST /v1/node/release/ready":
			var v struct {
				OperationID string `json:"operation_id"`
			}
			if json.NewDecoder(r.Body).Decode(&v) != nil || v.OperationID != fixtureReleaseID {
				t.Error("wrong WSS proof")
			}
			w.WriteHeader(http.StatusNoContent)
		default:
			http.Error(w, "not found", http.StatusNotFound)
		}
	})}
	go func() { _ = server.Serve(listener) }()
	defer server.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if image := readCurrentAgentNodeImage(ctx); image != "waylane-node-seed:bootstrap" {
		t.Fatalf("agent image: %s", image)
	}
	requested, err := submitAgentRelease(ctx, fixtureReleaseID, fixtureReleaseImage)
	if err != nil || requested.Status != "queued" {
		t.Fatalf("Agent release request: %+v %v", requested, err)
	}
	status, err := readAgentRelease(ctx)
	if err != nil || status.Phase != "awaiting_wss" {
		t.Fatalf("Agent progress: %+v %v", status, err)
	}
	if err := confirmAgentReleaseWSS(ctx, status); err != nil {
		t.Fatal(err)
	}
	// A rollback can only complete after the *previous* image also reconnects.
	status.Phase = "rollback_verify"
	if err := confirmAgentReleaseWSS(ctx, status); err != nil {
		t.Fatal(err)
	}
	if _, err := submitAgentRelease(ctx, fixtureReleaseID, "example:latest"); err == nil {
		t.Fatal("mutable release accepted")
	}
}

func TestNodeManualReconnectUsesOnlyAuthenticatedBinaryStream(t *testing.T) {
	uid := "11111111-1111-4111-8111-111111111111"
	desired, err := newControlMessage(uid, "node_action", "node/runtime", 1, map[string]any{
		"operation_id": fixtureReleaseID, "action": "reconnect", "image": "",
	})
	if err != nil {
		t.Fatal(err)
	}
	mock := &mockBinaryStream{inbox: []*controlEnvelope{
		{ProtocolVersion: 1, NodeUUID: uid, MessageID: "agent-hello", Kind: "hello_ack"}, desired,
	}}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	err = runControlStream(ctx, mock, identity{UUID: uid, Registered: true}, t.TempDir(), "/run/user/1000/docker.sock")
	if err == nil || err.Error() != "operator-requested WSS reconnect" {
		t.Fatalf("reconnect was not requested: %v", err)
	}
	seen := false
	for _, message := range mock.sent {
		if message.Kind == "node_action_report" {
			seen = message.CoreID == "node/runtime" && message.Generation == 1 &&
				message.Payload.GetFields()["phase"].GetStringValue() == "reconnecting"
		}
	}
	if !seen {
		t.Fatal("manual reconnect did not acknowledge old WSS before closing")
	}
}

func TestNodeRejectsMalformedAgentUnixRuntime(t *testing.T) {
	original := agentSocketPath
	agentSocketPath = filepath.Join(t.TempDir(), "agent.sock")
	t.Cleanup(func() { agentSocketPath = original })
	if err := os.WriteFile(agentSocketPath, []byte("not a private socket"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := readAgentRelease(context.Background()); !errors.Is(err, os.ErrNotExist) && err == nil {
		t.Fatal("untrusted Agent control file accepted")
	}
}

func TestNoReleaseCapabilityWhenStableAgentLacksVersionedEndpoint(t *testing.T) {
	original := agentSocketPath
	agentSocketPath = filepath.Join(t.TempDir(), "old-agent.sock")
	t.Cleanup(func() { agentSocketPath = original })
	listener, err := net.Listen("unix", agentSocketPath)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	if err := os.Chmod(agentSocketPath, 0600); err != nil {
		t.Fatal(err)
	}
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "old Agent API", http.StatusNotFound)
	})}
	go func() { _ = server.Serve(listener) }()
	defer server.Close()
	capabilities, err := controlTelemetryPayload(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	values, ok := capabilities["capabilities"].([]any)
	if !ok {
		t.Fatal("Node telemetry capabilities missing")
	}
	for _, value := range values {
		if value == "node.release.v1" || value == "node.reconnect.v1" {
			t.Fatalf("Old Agent without release API falsely advertised %q", value)
		}
	}
}
