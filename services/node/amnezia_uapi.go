package main

import (
	"bufio"
	"context"
	"errors"
	"net"
	"path/filepath"
	"strings"
	"time"
)

// awgPrivateUAPI exchanges a validated private request through a Unix socket
// shared only with the managed AWG child. This must never use an externally
// exposed management port or pass key material via docker CLI arguments.
// The socket's parent directory will be a Node-owned private bind mount.
func awgPrivateUAPI(ctx context.Context, socketPath string, request []byte) ([]string, error) {
	if !filepath.IsAbs(socketPath) || filepath.Clean(socketPath) != socketPath ||
		len(socketPath) > 108 || !strings.HasSuffix(socketPath, ".sock") {
		return nil, errors.New("invalid private AWG UAPI socket")
	}
	if len(request) < 6 || len(request) > maxAWGConfigBytes ||
		(!strings.HasPrefix(string(request), "set=1\n") && !strings.HasPrefix(string(request), "get=1\n")) ||
		!strings.HasSuffix(string(request), "\n\n") {
		return nil, errors.New("invalid AWG UAPI operation")
	}
	// Connections, writes and reads all respect the same short bounded timeout.
	ctx, cancel := context.WithTimeout(ctx, 5*time.Second)
	defer cancel()
	conn, err := (&net.Dialer{}).DialContext(ctx, "unix", socketPath)
	if err != nil {
		return nil, errors.New("AWG private UAPI socket unavailable")
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(5 * time.Second))
	// No key material should be attached to returned errors.
	n, err := conn.Write(request)
	if err != nil || n != len(request) {
		return nil, errors.New("AWG UAPI write failed")
	}
	reader := bufio.NewReaderSize(conn, 8192)
	result := make([]string, 0, 24)
	total := 0
	for {
		lineBytes, readErr := reader.ReadSlice('\n')
		if readErr != nil {
			return nil, errors.New("AWG UAPI response truncated")
		}
		total += len(lineBytes)
		if total > 64*1024 || len(result) > 8192 {
			return nil, errors.New("AWG UAPI response limit exceeded")
		}
		line := strings.TrimSuffix(string(lineBytes), "\n")
		if strings.ContainsRune(line, '\r') {
			return nil, errors.New("AWG UAPI response invalid")
		}
		if line == "" {
			break
		}
		result = append(result, line)
	}
	if len(result) == 0 || result[len(result)-1] != "errno=0" {
		return nil, errors.New("AWG UAPI operation failed")
	}
	return result[:len(result)-1], nil
}
