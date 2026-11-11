package main

import (
	"bytes"
	"context"
	"net"
	"path/filepath"
	"strings"
	"testing"
)

func TestPrivateAWGUnixUAPISocketExchange(t *testing.T) {
	path := filepath.Join(t.TempDir(), "awg0.sock")
	listener, err := net.Listen("unix", path)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	observed := make(chan string, 1)
	go func() {
		conn, e := listener.Accept()
		if e != nil {
			observed <- "accept failed"
			return
		}
		defer conn.Close()
		buf := make([]byte, 4096)
		n, e := conn.Read(buf)
		if e != nil {
			observed <- "read failed"
			return
		}
		observed <- string(buf[:n])
		_, _ = conn.Write([]byte("listen_port=51820\nerrno=0\n\n"))
	}()
	req := []byte("get=1\n\n")
	response, err := awgPrivateUAPI(context.Background(), path, req)
	if err != nil {
		t.Fatal(err)
	}
	if got := <-observed; got != string(req) {
		t.Fatal("UAPI request bytes changed")
	}
	if len(response) != 1 || response[0] != "listen_port=51820" {
		t.Fatalf("unexpected UAPI fields: %+v", response)
	}
}
func TestPrivateAWGUnixUAPIFailsClosed(t *testing.T) {
	for _, v := range []struct {
		path  string
		frame []byte
	}{
		{"relative.sock", []byte("get=1\n\n")},
		{"/tmp/unsupported", []byte("get=1\n\n")},
		{"/tmp/awg.sock", []byte("private_key=secret\n\n")},
		{"/tmp/awg.sock", bytes.Repeat([]byte("z"), maxAWGConfigBytes+1)},
	} {
		if _, err := awgPrivateUAPI(context.Background(), v.path, v.frame); err == nil {
			t.Fatal("invalid private UAPI request accepted")
		}
	}
	path := filepath.Join(t.TempDir(), "awg0.sock")
	listener, err := net.Listen("unix", path)
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	go func() {
		conn, e := listener.Accept()
		if e != nil {
			return
		}
		defer conn.Close()
		buf := make([]byte, 32)
		_, _ = conn.Read(buf)
		_, _ = conn.Write([]byte("private_key=should-never-appear-in-error\nerrno=2\n\n"))
	}()
	_, err = awgPrivateUAPI(context.Background(), path, []byte("get=1\n\n"))
	if err == nil || strings.Contains(err.Error(), "private_key") {
		t.Fatalf("error leaked UAPI response or failed open: %v", err)
	}
}
