//go:build linux

package wireguard

import (
	"bufio"
	"encoding/hex"
	"fmt"
	"net"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"golang.zx2c4.com/wireguard/wgctrl/wgtypes"
)

// A synthetic AWG-Go UAPI server exercises the exact wire format without
// requiring root, a host TUN device, or a real VPN connection.
func fakeAWGUAPI(t *testing.T, respond func(string) string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "test.sock")
	ln, err := net.Listen("unix", path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { ln.Close() })
	go func() {
		for {
			c, err := ln.Accept()
			if err != nil {
				return
			}
			go func() {
				defer c.Close()
				var parts []string
				reader := bufio.NewReader(c)
				for {
					l, err := reader.ReadString('\n')
					if err != nil {
						return
					}
					if strings.TrimSpace(l) == "" {
						break
					}
					parts = append(parts, strings.TrimSuffix(l, "\n"))
				}
				fmt.Fprint(c, respond(strings.Join(parts, "\n")))
			}()
		}
	}()
	return path
}
func TestAWGUAPIConfiguresAndReadsPeers(t *testing.T) {
	k := wgtypes.Key{1, 2, 3}
	configRequest := make(chan string, 1)
	peerHex := hex.EncodeToString(k[:])
	path := fakeAWGUAPI(t, func(query string) string {
		if strings.HasPrefix(query, "set=1") {
			configRequest <- query
			return "errno=0\n\n"
		}
		if strings.HasPrefix(query, "get=1") {
			return "listen_port=51820\npublic_key=" + peerHex + "\nrx_bytes=100\ntx_bytes=300\nallowed_ip=10.70.0.2/32\nlast_handshake_time_sec=1000\nerrno=0\n\n"
		}
		return "errno=-22\n\n"
	})
	cli := &awgUAPIClient{name: "awg9", settings: &AWG31{S1: 24, S2: 24, S3: 24, S4: 24, RandomTrailers: true}, socketPath: path}
	port := 51820
	_, subnet, err := net.ParseCIDR("10.70.0.2/32")
	if err != nil {
		t.Fatal(err)
	}
	if err = cli.ConfigureDevice("awg9", wgtypes.Config{PrivateKey: &k, ListenPort: &port, Peers: []wgtypes.PeerConfig{{PublicKey: k, AllowedIPs: []net.IPNet{*subnet}}}}); err != nil {
		t.Fatal(err)
	}
	select {
	case req := <-configRequest:
		for _, token := range []string{"private_key=" + peerHex, "listen_port=51820", "random_trailers=true", "public_key=" + peerHex, "allowed_ip=10.70.0.2/32"} {
			if !strings.Contains(req, token) {
				t.Errorf("missing %s", token)
			}
		}
	case <-time.After(time.Second):
		t.Fatal("no UAPI request")
	}
	d, err := cli.Device("awg9")
	if err != nil {
		t.Fatal(err)
	}
	if d.ListenPort != 51820 || len(d.Peers) != 1 || d.Peers[0].ReceiveBytes != 100 || d.Peers[0].TransmitBytes != 300 {
		t.Fatalf("unexpected AWG UAPI device stats: %+v", d)
	}
}
