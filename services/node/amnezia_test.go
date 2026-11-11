package main

import (
	"bytes"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"strings"
	"testing"
)

func awgTestKey(k byte) string { return base64.StdEncoding.EncodeToString(bytes.Repeat([]byte{k}, 32)) }
func awgTestConfig() awgWireConfig {
	return awgWireConfig{
		Engine: "amneziawg", InterfaceName: "awg0", PrivateKey: awgTestKey('a'), ListenPort: 51820,
		Address: []string{"10.90.0.1/24"},
		Amnezia: awg31Options{Jc: 4, Jmin: 24, Jmax: 48, S1: 16, S2: 16, S3: 16, S4: 16, H1: "100", H2: "200", H3: "300", H4: "400", I1: "<r 5><t>", HeaderProtectionKey: awgTestKey('b'), RandomTrailers: true, DisableCookies: true, ContentPaddingAddition: "2-12"},
		Peers:   []awgPeer{{PublicKey: awgTestKey('c'), PreSharedKey: awgTestKey('d'), AllowedIPs: []string{"10.90.0.2/32"}, Keepalive: 25}},
	}
}
func TestAmnezia31UAPICompatibleWithBackendConfig(t *testing.T) {
	config := awgTestConfig()
	raw, err := json.Marshal(config)
	if err != nil {
		t.Fatal(err)
	}
	parsed, err := awgDecodeStrict(raw)
	if err != nil {
		t.Fatal(err)
	}
	request, err := awgUAPISet(parsed)
	if err != nil {
		t.Fatal(err)
	}
	str := string(request)
	if !strings.HasPrefix(str, "set=1\n") || !strings.HasSuffix(str, "\n\n") {
		t.Fatal("invalid UAPI frame")
	}
	for _, line := range []string{
		"jc=4", "jmin=24", "jmax=48", "s1=16", "h1=100", "h4=400", "i1=<r 5><t>",
		"content_padding_addition=2-12", "random_trailers=true", "disable_cookies=true",
		"listen_port=51820", "replace_peers=true", "replace_allowed_ips=true", "allowed_ip=10.90.0.2/32", "persistent_keepalive_interval=25",
		"private_key=" + hex.EncodeToString(bytes.Repeat([]byte{'a'}, 32)),
		"public_key=" + hex.EncodeToString(bytes.Repeat([]byte{'c'}, 32)),
		"preshared_key=" + hex.EncodeToString(bytes.Repeat([]byte{'d'}, 32)),
		"header_protection_key=" + hex.EncodeToString(bytes.Repeat([]byte{'b'}, 32)),
	} {
		if !strings.Contains(str, "\n"+line+"\n") {
			t.Errorf("missing UAPI setting %q", line)
		}
	}
	if strings.Contains(str, awgTestKey('a')) || strings.Contains(str, "10.90.0.1") {
		t.Fatal("config representation leaked into UAPI")
	}
}
func TestAmnezia31ValidationFailsClosed(t *testing.T) {
	cases := map[string]func(*awgWireConfig){
		"missing engine":         func(c *awgWireConfig) { c.Engine = "" },
		"invalid iface":          func(c *awgWireConfig) { c.InterfaceName = "../delete" },
		"invalid port":           func(c *awgWireConfig) { c.ListenPort = -1 },
		"unsupported global PSK": func(c *awgWireConfig) { c.PreSharedKey = awgTestKey('z') },
		"invalid secret":         func(c *awgWireConfig) { c.PrivateKey = "invalid" },
		"invalid CIDR":           func(c *awgWireConfig) { c.Address = []string{"abc"} },
		"overlap headers":        func(c *awgWireConfig) { c.Amnezia.H2 = "99-101" },
		"invalid CPS":            func(c *awgWireConfig) { c.Amnezia.I1 = "<t>\nprivate_key=injected" },
		"invalid header key":     func(c *awgWireConfig) { c.Amnezia.HeaderProtectionKey = "invalid" },
		"header S1 too small":    func(c *awgWireConfig) { c.Amnezia.S1 = 11 },
		"jmin exceeds jmax":      func(c *awgWireConfig) { c.Amnezia.Jmin = 50 },
		"invalid peer":           func(c *awgWireConfig) { c.Peers[0].PublicKey = "fake" },
		"peer CIDR":              func(c *awgWireConfig) { c.Peers[0].AllowedIPs = []string{"oops"} },
		"peer keepalive":         func(c *awgWireConfig) { c.Peers[0].Keepalive = -1 },
		"duplicate peer":         func(c *awgWireConfig) { c.Peers = append(c.Peers, c.Peers[0]) },
	}
	for name, change := range cases {
		t.Run(name, func(t *testing.T) {
			c := awgTestConfig()
			change(&c)
			raw, _ := json.Marshal(c)
			if _, err := awgDecodeStrict(raw); err == nil {
				t.Fatal("invalid AWG config accepted")
			}
			if _, err := awgUAPISet(c); err == nil {
				t.Fatal("invalid AWG config allowed into UAPI")
			}
		})
	}
}
func TestAmnezia31RejectsUnknownOrMultipleJSON(t *testing.T) {
	config := awgTestConfig()
	raw, _ := json.Marshal(config)
	for _, bad := range [][]byte{nil, []byte("[]"), []byte("null"), append(raw, []byte(` {}`)...), bytes.Replace(raw, []byte(`"engine":"amneziawg"`), []byte(`"engine":"amneziawg","random_shell":"yes"`), 1), bytes.Repeat([]byte("x"), maxAWGConfigBytes+1)} {
		if _, err := awgDecodeStrict(bad); err == nil {
			t.Fatalf("malformed AWG JSON accepted %q", string(bad[:min(len(bad), 40)]))
		}
	}
}
func TestAmneziaPlainWireGuardDoesNotInheritAWGFields(t *testing.T) {
	cfg := awgTestConfig()
	cfg.Engine = "wireguard"
	cfg.Amnezia = awg31Options{}
	raw, _ := json.Marshal(cfg)
	if _, err := awgDecodeStrict(raw); err != nil {
		t.Fatal("an explicit unobfuscated WireGuard profile must remain compatible:", err)
	}
	request, err := awgUAPISet(cfg)
	if err != nil || !bytes.Contains(request, []byte("listen_port=51820")) {
		t.Fatal("plain WireGuard UAPI conversion failed:", err)
	}
	cfg.Amnezia.Jc = 4
	raw, _ = json.Marshal(cfg)
	if _, err := awgDecodeStrict(raw); err == nil {
		t.Fatal("obfuscated fields must never be accepted as plain WireGuard")
	}
}
