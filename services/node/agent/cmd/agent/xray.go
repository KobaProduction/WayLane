package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

const xrayChildName = "waylane-xray"
const xrayDefaultVersion = "26.10.21"

var xrayTagRE = regexp.MustCompile(`^\d{2}\.\d{1,2}\.\d{1,2}$`)

type xrayDesired struct {
	Configured        bool              `json:"configured"`
	Generation        int               `json:"generation"`
	AppliedGeneration int               `json:"applied_generation"`
	Action            string            `json:"action"`
	ImageVersion      string            `json:"image_version"`
	Region            string            `json:"region"`
	Config            json.RawMessage   `json:"config"`
	TLSCertificates   []xrayTLSMaterial `json:"tls_certificates"`
}

type xrayTLSMaterial struct {
	InboundTag  string `json:"inbound_tag"`
	Revision    int    `json:"revision"`
	Certificate string `json:"certificate"`
	PrivateKey  string `json:"private_key"`
}

type xrayLocalState struct {
	Generation   int             `json:"generation"`
	ImageVersion string          `json:"image_version"`
	Config       json.RawMessage `json:"config"`
	Status       string          `json:"status"`
	Error        string          `json:"error,omitempty"`
}

func xrayImage(version string) (string, error) {
	if !xrayTagRE.MatchString(version) {
		return "", fmt.Errorf("invalid pinned official Xray version")
	}
	return "ghcr.io/xtls/xray-core:" + version, nil
}

func validXrayConfig(payload json.RawMessage) error {
	if len(payload) == 0 || len(payload) > 512000 {
		return errors.New("Xray config absent or too large")
	}
	var document map[string]json.RawMessage
	if err := json.Unmarshal(payload, &document); err != nil {
		return fmt.Errorf("invalid Xray JSON: %w", err)
	}
	var inbounds, outbounds []json.RawMessage
	if err := json.Unmarshal(document["inbounds"], &inbounds); err != nil || len(inbounds) == 0 {
		return errors.New("Xray needs nonempty inbounds")
	}
	if err := json.Unmarshal(document["outbounds"], &outbounds); err != nil || len(outbounds) == 0 {
		return errors.New("Xray needs nonempty outbounds")
	}
	return nil
}

func privateXrayFile(dir, filename string) (string, error) {
	if dir == "" || !filepath.IsAbs(dir) {
		return "", errors.New("private absolute Agent state directory required")
	}
	path := filepath.Join(dir, "xray")
	if err := os.MkdirAll(path, 0700); err != nil {
		return "", err
	}
	return filepath.Join(path, filename), nil
}

func writePrivateFile(path string, content []byte) error {
	if err := os.WriteFile(path+".next", content, 0600); err != nil {
		return err
	}
	return os.Rename(path+".next", path)
}

// materializeXrayTLS writes private key/certificate pairs to generation-scoped
// files that are read-only in the child. The ordinary desired config, Agent
// state JSON and the admin API NEVER contain the injected private key.
func materializeXrayTLS(dir string, generation int, raw json.RawMessage,
	materials []xrayTLSMaterial) (json.RawMessage, error) {
	if len(materials) == 0 {
		return raw, nil
	}
	if generation < 1 || len(materials) > 64 {
		return nil, errors.New("invalid Xray certificate generation")
	}
	var config map[string]any
	if err := json.Unmarshal(raw, &config); err != nil {
		return nil, err
	}
	inbounds, ok := config["inbounds"].([]any)
	if !ok {
		return nil, errors.New("Xray inbounds missing")
	}
	path, err := privateXrayFile(dir, "certificates")
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(path, 0700); err != nil {
		return nil, err
	}
	release := filepath.Join(path, fmt.Sprintf("revision-%d", generation))
	if err := os.MkdirAll(release, 0700); err != nil {
		return nil, err
	}
	seen := make(map[string]bool)
	for _, material := range materials {
		if seen[material.InboundTag] || material.InboundTag == "" || material.Revision < 1 {
			return nil, errors.New("duplicate or invalid managed TLS inbound")
		}
		seen[material.InboundTag] = true
		if len(material.Certificate) > 65536 || len(material.PrivateKey) > 16384 {
			return nil, errors.New("managed TLS material too large")
		}
		pair, err := tls.X509KeyPair([]byte(material.Certificate), []byte(material.PrivateKey))
		if err != nil {
			return nil, errors.New("managed TLS key does not match certificate")
		}
		leaf, err := x509.ParseCertificate(pair.Certificate[0])
		if err != nil || !time.Now().Before(leaf.NotAfter) {
			return nil, errors.New("managed TLS certificate expired or invalid")
		}
		var matched map[string]any
		for _, value := range inbounds {
			inbound, ok := value.(map[string]any)
			if !ok || inbound["tag"] != material.InboundTag {
				continue
			}
			if matched != nil {
				return nil, errors.New("duplicate Xray inbound tag")
			}
			matched = inbound
		}
		if matched == nil {
			return nil, errors.New("managed TLS inbound missing")
		}
		stream, ok := matched["streamSettings"].(map[string]any)
		if !ok || stream["security"] != "tls" {
			return nil, errors.New("managed TLS inbound is not TLS")
		}
		fingerprint := sha256.Sum256([]byte(material.InboundTag))
		name := hex.EncodeToString(fingerprint[:12])
		certificateName := name + ".pem"
		keyName := name + ".key"
		if err := writePrivateFile(filepath.Join(release, certificateName), []byte(material.Certificate)); err != nil {
			return nil, err
		}
		if err := writePrivateFile(filepath.Join(release, keyName), []byte(material.PrivateKey)); err != nil {
			return nil, err
		}
		settings, ok := stream["tlsSettings"].(map[string]any)
		if !ok {
			settings = map[string]any{}
			stream["tlsSettings"] = settings
		}
		settings["certificates"] = []map[string]any{{
			"certificateFile": fmt.Sprintf("/etc/waylane-xray/certificates/revision-%d/%s", generation, certificateName),
			"keyFile":         fmt.Sprintf("/etc/waylane-xray/certificates/revision-%d/%s", generation, keyName),
		}}
	}
	return json.Marshal(config)
}

func readXrayState(dir string) (xrayLocalState, error) {
	path, err := privateXrayFile(dir, "state.json")
	if err != nil {
		return xrayLocalState{}, err
	}
	data, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return xrayLocalState{}, nil
	}
	if err != nil {
		return xrayLocalState{}, err
	}
	var state xrayLocalState
	err = json.Unmarshal(data, &state)
	return state, err
}

func saveXrayState(dir string, state xrayLocalState) error {
	path, err := privateXrayFile(dir, "state.json")
	if err != nil {
		return err
	}
	contents, err := json.Marshal(state)
	if err != nil {
		return err
	}
	return writePrivateFile(path, contents)
}

func dockerXray(ctx context.Context, socket string, timeout time.Duration, args ...string) error {
	if socket != "/run/user/1000/docker.sock" {
		return errors.New("unexpected Docker socket")
	}
	c, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	cmd := exec.CommandContext(c, "docker", append([]string{"--host", "unix://" + socket}, args...)...)
	// Avoid exposing Xray credentials/config in command output.
	var stderr bytes.Buffer
	cmd.Stdout = io.Discard
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		detail := strings.ToLower(stderr.String())
		if len(detail) > 180 {
			detail = detail[:180]
		}
		if strings.Contains(detail, "not found") || strings.Contains(detail, "no such container") {
			return fmt.Errorf("docker Xray container missing: %w", err)
		}
		return fmt.Errorf("Xray Docker operation failed: %w", err)
	}
	return nil
}

func startXrayContainer(ctx context.Context, socket, image, configFile string) error {
	// Root here is only inside the private rootless Docker's user namespace,
	// never the host root; required to read mode-0600 mounted Xray config.
	return dockerXray(ctx, socket, 45*time.Second,
		"run", "-d", "--name", xrayChildName,
		"--restart", "unless-stopped", "--network", "host",
		"--read-only", "--cap-drop", "ALL",
		"--security-opt", "no-new-privileges",
		"--user", "0:0",
		"--mount", "type=bind,source="+configFile+",target=/usr/local/etc/xray/config.json,readonly",
		"--mount", "type=bind,source="+filepath.Dir(configFile)+",target=/etc/waylane-xray,readonly",
		image, "run", "-config", "/usr/local/etc/xray/config.json")
}

func applyXrayDesired(ctx context.Context, socket, dir string, current xrayLocalState, desired xrayDesired) (xrayLocalState, error) {
	next := current
	next.Generation = desired.Generation
	next.Status = "error"
	next.Error = ""
	if desired.Action == "stop" {
		err := dockerXray(ctx, socket, 20*time.Second, "stop", "-t", "5", xrayChildName)
		if err == nil {
			next.Status = "stopped"
		}
		return next, err
	}
	if desired.Action == "restart" {
		err := dockerXray(ctx, socket, 25*time.Second, "restart", "-t", "5", xrayChildName)
		if err == nil {
			next.Status = "running"
		}
		return next, err
	}
	if desired.Action != "deploy" && desired.Action != "update" {
		return next, errors.New("Xray action not allowed")
	}
	image, err := xrayImage(desired.ImageVersion)
	if err != nil {
		return next, err
	}
	if err := validXrayConfig(desired.Config); err != nil {
		return next, err
	}
	materialized, err := materializeXrayTLS(dir, desired.Generation, desired.Config, desired.TLSCertificates)
	if err != nil {
		return next, err
	}
	// Pull and validate the incoming image before touching an active core.
	if err := dockerXray(ctx, socket, 180*time.Second, "pull", image); err != nil {
		return next, err
	}
	configFile, err := privateXrayFile(dir, "config.json")
	if err != nil {
		return next, err
	}
	staged, err := privateXrayFile(dir, "candidate.json")
	if err != nil {
		return next, err
	}
	if err := writePrivateFile(staged, materialized); err != nil {
		return next, err
	}
	err = dockerXray(ctx, socket, 45*time.Second,
		"run", "--rm", "--network", "host", "--read-only",
		"--cap-drop", "ALL", "--security-opt", "no-new-privileges",
		"--user", "0:0",
		"--mount", "type=bind,source="+staged+",target=/usr/local/etc/xray/config.json,readonly",
		"--mount", "type=bind,source="+filepath.Dir(staged)+",target=/etc/waylane-xray,readonly",
		image, "run", "-test", "-config", "/usr/local/etc/xray/config.json")
	if err != nil {
		return next, fmt.Errorf("Xray configuration rejected: %w", err)
	}
	if err := dockerXray(ctx, socket, 20*time.Second, "stop", "-t", "5", xrayChildName); err != nil {
		// An absent pre-existing child is normal on a first deployment.
		if !strings.Contains(err.Error(), "container missing") {
			return next, err
		}
	}
	if err := dockerXray(ctx, socket, 15*time.Second, "rm", xrayChildName); err != nil && !strings.Contains(err.Error(), "container missing") {
		return next, err
	}
	previousRuntimeConfig, previousConfigErr := os.ReadFile(configFile)
	if previousConfigErr != nil && !errors.Is(previousConfigErr, os.ErrNotExist) {
		return next, previousConfigErr
	}
	restorePrevious := func() {
		if current.ImageVersion == "" || len(current.Config) == 0 {
			return
		}
		oldImage, valid := xrayImage(current.ImageVersion)
		if valid != nil {
			return
		}
		// The prior materialized runtime file references its own immutable
		// generation-scoped TLS files. Never restore raw JSON in its place.
		if len(previousRuntimeConfig) > 0 {
			_ = writePrivateFile(configFile, previousRuntimeConfig)
			_ = startXrayContainer(ctx, socket, oldImage, configFile)
		}
	}
	if err := writePrivateFile(configFile, materialized); err != nil {
		restorePrevious()
		return next, err
	}
	if err := startXrayContainer(ctx, socket, image, configFile); err != nil {
		restorePrevious()
		return next, err
	}
	// The Docker API returning a container ID does not prove Xray booted.
	// Require it to remain running briefly before accepting the generation.
	select {
	case <-time.After(2 * time.Second):
	case <-ctx.Done():
		return next, ctx.Err()
	}
	checkCtx, cancel := context.WithTimeout(ctx, 6*time.Second)
	defer cancel()
	check := exec.CommandContext(checkCtx, "docker", "--host", "unix://"+socket,
		"inspect", "--format", "{{.State.Running}}", xrayChildName)
	output, checkErr := check.Output()
	if checkErr != nil || strings.TrimSpace(string(output)) != "true" {
		_ = dockerXray(ctx, socket, 15*time.Second, "rm", "-f", xrayChildName)
		restorePrevious()
		return next, errors.New("Xray failed its post-start readiness check")
	}
	next.Status = "running"
	next.ImageVersion = desired.ImageVersion
	next.Config = append(json.RawMessage(nil), desired.Config...)
	return next, nil
}

func getXrayDesired(ctx context.Context, client *http.Client, origin string, ident identity) (xrayDesired, error) {
	req, err := http.NewRequestWithContext(ctx, "GET", origin+"/api/agent/xray/desired?node_uuid="+ident.UUID, nil)
	if err != nil {
		return xrayDesired{}, err
	}
	req.Header.Set("X-Agent-Secret", ident.Secret)
	resp, err := client.Do(req)
	if err != nil {
		return xrayDesired{}, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return xrayDesired{}, fmt.Errorf("Xray desired-state HTTP %d", resp.StatusCode)
	}
	var desired xrayDesired
	err = json.NewDecoder(io.LimitReader(resp.Body, 2000000)).Decode(&desired)
	return desired, err
}

func reportXray(ctx context.Context, client *http.Client, origin string, ident identity, state xrayLocalState) error {
	content, err := json.Marshal(map[string]any{
		"node_uuid": ident.UUID, "generation": state.Generation,
		"status": state.Status, "error": state.Error})
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, "POST", origin+"/api/agent/xray/report", bytes.NewReader(content))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-Agent-Secret", ident.Secret)
	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return fmt.Errorf("Xray report HTTP %d", resp.StatusCode)
	}
	return nil
}

func synchronizeXray(ctx context.Context, client *http.Client, origin string, ident identity, dir, socket string) error {
	desired, err := getXrayDesired(ctx, client, origin, ident)
	if err != nil {
		return err
	}
	if !desired.Configured {
		return nil
	}
	local, err := readXrayState(dir)
	if err != nil {
		return err
	}
	if desired.Generation != local.Generation {
		result, applyErr := applyXrayDesired(ctx, socket, dir, local, desired)
		if applyErr != nil {
			result.Status = "error"
			// Never return raw Docker output to the panel.
			result.Error = applyErr.Error()
			if len(result.Error) > 290 {
				result.Error = result.Error[:290]
			}
		}
		if err := saveXrayState(dir, result); err != nil {
			return err
		}
		local = result
	}
	// Re-reporting after an outage is idempotent; commands are not re-run.
	return reportXray(ctx, client, origin, ident, local)
}
