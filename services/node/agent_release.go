package main

// The Node forwards explicit Backend release intent to its stable Agent's
// versioned PRIVATE Unix API. The Agent remains alive while it replaces this
// Node container and verifies that the replacement regained WSS connectivity.
import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"os"
	"regexp"
	"time"
)

const privateAgentSocket = "/var/lib/waylane-agent/control/agent.sock"
const privateAgentOrigin = "http://waylane-agent"

// The Unix endpoint is fixed in production. Tests substitute a private socket
// without modifying Agent installation state or host Docker permissions.
var agentSocketPath = privateAgentSocket

var approvedNodeDigest = regexp.MustCompile(`^ghcr\.io/kobaproduction/waylane-node@sha256:[0-9a-f]{64}$`)
var releaseOperationUUID = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`)

type agentReleaseStatus struct {
	APIVersion    int    `json:"api_version"`
	OperationID   string `json:"operation_id"`
	Image         string `json:"image"`
	PreviousImage string `json:"previous_image"`
	Status        string `json:"status"`
	Phase         string `json:"phase"`
	Progress      int    `json:"progress"`
	ErrorCode     string `json:"error_code"`
}

func agentReleaseClient() (*http.Client, error) {
	stat, err := os.Lstat(agentSocketPath)
	if err != nil || stat.Mode()&os.ModeSocket == 0 || stat.Mode().Perm()&0077 != 0 {
		return nil, errors.New("private Agent Unix socket unavailable or insecure")
	}
	transport := &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
		return (&net.Dialer{Timeout: 3 * time.Second}).DialContext(ctx, "unix", agentSocketPath)
	}}
	return &http.Client{Transport: transport, Timeout: 6 * time.Second}, nil
}
func agentReleaseRequest(ctx context.Context, method, path string, body any) (agentReleaseStatus, int, error) {
	client, err := agentReleaseClient()
	if err != nil {
		return agentReleaseStatus{}, 0, err
	}
	defer client.CloseIdleConnections()
	var raw io.Reader
	if body != nil {
		encoded, e := json.Marshal(body)
		if e != nil {
			return agentReleaseStatus{}, 0, e
		}
		raw = bytes.NewReader(encoded)
	}
	req, err := http.NewRequestWithContext(ctx, method, privateAgentOrigin+path, raw)
	if err != nil {
		return agentReleaseStatus{}, 0, err
	}
	req.Header.Set("Content-Type", "application/json")
	response, err := client.Do(req)
	if err != nil {
		return agentReleaseStatus{}, 0, err
	}
	defer response.Body.Close()
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return agentReleaseStatus{}, response.StatusCode, errors.New("Agent rejected private release operation")
	}
	var status agentReleaseStatus
	if response.StatusCode == http.StatusNoContent {
		return status, response.StatusCode, nil
	}
	bounded, e := io.ReadAll(io.LimitReader(response.Body, 4097))
	if e != nil || len(bounded) > 4096 || json.Unmarshal(bounded, &status) != nil || status.APIVersion != 1 {
		return agentReleaseStatus{}, response.StatusCode, errors.New("Agent returned invalid private release state")
	}
	return status, response.StatusCode, nil
}
func readAgentRelease(ctx context.Context) (agentReleaseStatus, error) {
	result, _, err := agentReleaseRequest(ctx, http.MethodGet, "/v1/node/release", nil)
	return result, err
}
func submitAgentRelease(ctx context.Context, id, image string) (agentReleaseStatus, error) {
	if !releaseOperationUUID.MatchString(id) || !approvedNodeDigest.MatchString(image) {
		return agentReleaseStatus{}, errors.New("invalid pinned Agent release request")
	}
	response, _, err := agentReleaseRequest(ctx, http.MethodPut, "/v1/node/release", map[string]any{
		"api_version": 1, "operation_id": id, "image": image,
	})
	return response, err
}
func confirmAgentReleaseWSS(ctx context.Context, status agentReleaseStatus) error {
	if status.Status != "running" || status.Phase != "awaiting_wss" || !releaseOperationUUID.MatchString(status.OperationID) {
		return nil
	}
	_, _, err := agentReleaseRequest(ctx, http.MethodPost, "/v1/node/release/ready", map[string]any{
		"api_version": 1, "operation_id": status.OperationID,
	})
	return err
}
func agentReleaseTelemetry(ctx context.Context) (map[string]any, error) {
	status, err := readAgentRelease(ctx)
	if err != nil {
		return nil, err
	}
	if !releaseOperationUUID.MatchString(status.OperationID) {
		return nil, nil
	}
	if status.Progress < 0 || status.Progress > 100 || !approvedNodeDigest.MatchString(status.Image) {
		return nil, errors.New("invalid Agent release status")
	}
	return map[string]any{
		"operation_id": status.OperationID, "image": status.Image,
		"previous_image": status.PreviousImage,
		"status":         status.Status, "phase": status.Phase, "progress": status.Progress,
		"error_code": status.ErrorCode,
	}, nil
}

// Node image is non-secret metadata, independently reported by the private Agent.
func readCurrentAgentNodeImage(ctx context.Context) string {
	client, err := agentReleaseClient()
	if err != nil {
		return ""
	}
	defer client.CloseIdleConnections()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, privateAgentOrigin+"/v1/node", nil)
	if err != nil {
		return ""
	}
	res, err := client.Do(req)
	if err != nil {
		return ""
	}
	defer res.Body.Close()
	if res.StatusCode != http.StatusOK {
		return ""
	}
	var doc struct {
		APIVersion int    `json:"api_version"`
		Image      string `json:"image"`
	}
	if json.NewDecoder(io.LimitReader(res.Body, 4096)).Decode(&doc) != nil || doc.APIVersion != 1 {
		return ""
	}
	if doc.Image == "waylane-node-seed:bootstrap" || approvedNodeDigest.MatchString(doc.Image) {
		return doc.Image
	}
	return ""
}
