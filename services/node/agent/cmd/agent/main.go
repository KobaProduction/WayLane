package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"
)

const version = "0.1.0"

type identity struct {
	UUID       string
	Secret     string
	Registered bool
}
type panelResponse struct{ Status string }

func newIdentity() (identity, error) {
	raw := make([]byte, 48)
	if _, err := rand.Read(raw); err != nil {
		return identity{}, err
	}
	raw[6] = (raw[6] & 0x0f) | 0x40
	raw[8] = (raw[8] & 0x3f) | 0x80
	uuid := fmt.Sprintf("%x-%x-%x-%x-%x", raw[0:4], raw[4:6], raw[6:8], raw[8:10], raw[10:16])
	return identity{UUID: uuid, Secret: base64.RawURLEncoding.EncodeToString(raw[16:48])}, nil
}

func writeIdentity(dir string, value identity) error {
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	stat, err := os.Lstat(dir)
	if err != nil || !stat.IsDir() || stat.Mode().Perm()&0077 != 0 {
		return errors.New("private state directory required")
	}
	path := filepath.Join(dir, "identity.json")
	if info, err := os.Lstat(path); err == nil {
		if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 {
			return errors.New("private identity file required")
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	data, err := json.Marshal(value)
	if err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, ".identity-*")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	if err = tmp.Chmod(0600); err == nil {
		_, err = tmp.Write(data)
	}
	if err == nil {
		err = tmp.Sync()
	}
	if e := tmp.Close(); err == nil {
		err = e
	}
	if err != nil {
		return err
	}
	return os.Rename(tmp.Name(), path)
}

func loadIdentity(dir string) (identity, bool, error) {
	path := filepath.Join(dir, "identity.json")
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return identity{}, false, nil
	}
	if err != nil {
		return identity{}, false, err
	}
	if !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 {
		return identity{}, false, errors.New("identity file permissions invalid")
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return identity{}, false, err
	}
	var value identity
	if err = json.Unmarshal(b, &value); err != nil {
		return identity{}, false, err
	}
	if len(value.UUID) != 36 || len(value.Secret) < 43 {
		return identity{}, false, errors.New("invalid local identity")
	}
	return value, true, nil
}

func panelOrigin(s string) (string, error) {
	u, err := url.Parse(s)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return "", errors.New("panel must be an HTTPS origin with a trusted CA")
	}
	return strings.TrimSuffix(u.String(), "/"), nil
}

func checkRootlessDocker(ctx context.Context, sock string) error {
	info, err := os.Stat(sock)
	if err != nil || info.Mode()&os.ModeSocket == 0 {
		return errors.New("rootless Docker socket unavailable")
	}
	if sock == "/var/run/docker.sock" || sock == "/run/docker.sock" {
		return errors.New("system Docker socket forbidden")
	}
	ctx, cancel := context.WithTimeout(ctx, 8*time.Second)
	defer cancel()
	output, err := exec.CommandContext(ctx, "docker", "--host", "unix://"+sock, "info", "--format", "{{json .SecurityOptions}}").Output()
	if err != nil {
		return fmt.Errorf("rootless Docker health check: %w", err)
	}
	if !bytes.Contains(output, []byte("rootless")) {
		return errors.New("Docker daemon did not report rootless")
	}
	return nil
}

func reportIP() (string, string) {
	list, err := net.InterfaceAddrs()
	if err != nil {
		return "", ""
	}
	var ipv4, ipv6 string
	for _, addr := range list {
		ipnet, ok := addr.(*net.IPNet)
		if !ok {
			continue
		}
		ip := ipnet.IP
		if ip.IsLoopback() || ip.IsPrivate() || ip.IsLinkLocalUnicast() || !ip.IsGlobalUnicast() {
			continue
		}
		if ip.To4() != nil && ipv4 == "" {
			ipv4 = ip.String()
		} else if ip.To4() == nil && ipv6 == "" {
			ipv6 = ip.String()
		}
	}
	return ipv4, ipv6
}

func post(ctx context.Context, client *http.Client, origin, path, header, credential string, input any) (panelResponse, error) {
	encoded, err := json.Marshal(input)
	if err != nil {
		return panelResponse{}, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, origin+path, bytes.NewReader(encoded))
	if err != nil {
		return panelResponse{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set(header, credential)
	resp, err := client.Do(req)
	if err != nil {
		return panelResponse{}, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(io.LimitReader(resp.Body, 4096))
	if err != nil {
		return panelResponse{}, err
	}
	if resp.StatusCode >= 300 {
		return panelResponse{}, fmt.Errorf("panel %s returned HTTP %d", path, resp.StatusCode)
	}
	var answer panelResponse
	if err := json.Unmarshal(data, &answer); err != nil {
		return panelResponse{}, err
	}
	if answer.Status != "pending" && answer.Status != "approved" {
		return panelResponse{}, errors.New("unexpected node status")
	}
	return answer, nil
}

func optionalIP(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func run(ctx context.Context) error {
	if os.Geteuid() == 0 {
		return errors.New("WayLane Agent must never run as root")
	}
	origin, err := panelOrigin(os.Getenv("WAYLANE_PANEL_URL"))
	if err != nil {
		return err
	}
	token := os.Getenv("WAYLANE_DISCOVERY_TOKEN")
	if !strings.HasPrefix(token, "wl_disc_") || len(token) > 100 {
		return errors.New("missing discovery token")
	}
	state := os.Getenv("WAYLANE_AGENT_STATE_DIR")
	if state == "" {
		return errors.New("state directory required")
	}
	socket := os.Getenv("WAYLANE_ROOTLESS_SOCKET")
	if err := checkRootlessDocker(ctx, socket); err != nil {
		return err
	}
	ident, exists, err := loadIdentity(state)
	if err != nil {
		return err
	}
	if !exists {
		ident, err = newIdentity()
		if err != nil {
			return err
		}
		if err = writeIdentity(state, ident); err != nil {
			return err
		}
	}
	name := os.Getenv("WAYLANE_AGENT_NAME")
	if name == "" || len(name) > 128 {
		return errors.New("invalid agent name")
	}
	client := &http.Client{
		Timeout: 15 * time.Second,
		// Never forward discovery/agent credentials to a redirected origin.
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
	}
	if !ident.Registered {
		reply, err := post(ctx, client, origin, "/api/agent/enroll", "X-Discovery-Token", token, map[string]any{
			"node_uuid": ident.UUID, "agent_secret": ident.Secret, "display_name": name, "agent_version": version,
		})
		if err != nil {
			return err
		}
		ident.Registered = true
		if err = writeIdentity(state, ident); err != nil {
			return err
		}
		log.Printf("registered node=%s status=%s", ident.UUID, reply.Status)
	}
	heartbeat := func() error {
		v4, v6 := reportIP()
		reply, err := post(ctx, client, origin, "/api/agent/heartbeat", "X-Agent-Secret", ident.Secret, map[string]any{
			"node_uuid": ident.UUID, "agent_version": version, "reported_ipv4": optionalIP(v4),
			"reported_ipv6": optionalIP(v6), "capabilities": []string{"docker.rootless.inspect"},
		})
		if err != nil {
			return err
		}
		log.Printf("node=%s status=%s", ident.UUID, reply.Status)
		return nil
	}
	if err := heartbeat(); err != nil {
		return err
	}
	ticker := time.NewTicker(60 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			if err := checkRootlessDocker(ctx, socket); err != nil {
				return err
			}
			if err := heartbeat(); err != nil {
				return err
			}
		}
	}
}

func main() {
	if err := run(context.Background()); err != nil {
		log.Fatalf("WayLane Agent stopped: %v", err)
	}
}
