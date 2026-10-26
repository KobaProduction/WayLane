//go:build linux

package wireguard

import (
	"bufio"
	"bytes"
	"encoding/hex"
	"errors"
	"fmt"
	"net"
	"strconv"
	"strings"
	"sync"
	"time"

	"golang.zx2c4.com/wireguard/wgctrl/wgtypes"
)

const amneziaSocketDir = "/var/run/amneziawg"

// awgUAPIClient implements the subset of wgctrl needed by the existing
// WireGuard peer-sync/statistics subsystem, over AmneziaWG-Go 3.1 UAPI.
type awgUAPIClient struct {
	mu              sync.Mutex
	name            string
	settings        *AWG31
	settingsApplied bool
	socketPath      string // test seam, production uses amneziaSocketDir
}

func (c *awgUAPIClient) exchange(operation string) ([]string, error) {
	path := c.socketPath
	if path == "" {
		path = amneziaSocketDir + "/" + c.name + ".sock"
	}
	conn, err := net.DialTimeout("unix", path, 3*time.Second)
	if err != nil {
		return nil, fmt.Errorf("awg UAPI connection: %w", err)
	}
	defer conn.Close()
	if err := conn.SetDeadline(time.Now().Add(5 * time.Second)); err != nil {
		return nil, err
	}
	if _, err := conn.Write([]byte(operation + "\n")); err != nil {
		return nil, err
	}
	var reply []string
	reader := bufio.NewReader(conn)
	for {
		line, err := reader.ReadString('\n')
		if err != nil {
			return nil, fmt.Errorf("awg UAPI read: %w", err)
		}
		line = strings.TrimSuffix(line, "\n")
		if line == "" {
			break
		}
		reply = append(reply, line)
		if len(reply) > 50000 {
			return nil, errors.New("awg UAPI response too large")
		}
	}
	if len(reply) == 0 || reply[len(reply)-1] != "errno=0" {
		return nil, fmt.Errorf("awg UAPI rejected operation")
	}
	return reply[:len(reply)-1], nil
}

func (c *awgUAPIClient) ConfigureDevice(name string, cfg wgtypes.Config) error {
	if name != c.name {
		return fmt.Errorf("unexpected awg interface name")
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	var body bytes.Buffer
	body.WriteString("set=1\n")
	if !c.settingsApplied {
		fields, err := c.settings.UAPIFields()
		if err != nil {
			return err
		}
		for _, f := range fields {
			fmt.Fprintln(&body, f)
		}
	}
	if cfg.PrivateKey != nil {
		fmt.Fprintf(&body, "private_key=%s\n", hex.EncodeToString(cfg.PrivateKey[:]))
	}
	if cfg.ListenPort != nil {
		fmt.Fprintf(&body, "listen_port=%d\n", *cfg.ListenPort)
	}
	if cfg.FirewallMark != nil {
		fmt.Fprintf(&body, "fwmark=%d\n", *cfg.FirewallMark)
	}
	if cfg.ReplacePeers {
		body.WriteString("replace_peers=true\n")
	}
	for _, peer := range cfg.Peers {
		fmt.Fprintf(&body, "public_key=%s\n", hex.EncodeToString(peer.PublicKey[:]))
		if peer.Remove {
			body.WriteString("remove=true\n")
			continue
		}
		if peer.UpdateOnly {
			body.WriteString("update_only=true\n")
		}
		if peer.PresharedKey != nil {
			fmt.Fprintf(&body, "preshared_key=%s\n", hex.EncodeToString(peer.PresharedKey[:]))
		}
		if peer.Endpoint != nil {
			fmt.Fprintf(&body, "endpoint=%s\n", peer.Endpoint.String())
		}
		if peer.ReplaceAllowedIPs {
			body.WriteString("replace_allowed_ips=true\n")
		}
		for _, ip := range peer.AllowedIPs {
			fmt.Fprintf(&body, "allowed_ip=%s\n", ip.String())
		}
		if peer.PersistentKeepaliveInterval != nil {
			fmt.Fprintf(&body, "persistent_keepalive_interval=%d\n", int(peer.PersistentKeepaliveInterval.Seconds()))
		}
	}
	if _, err := c.exchange(body.String()); err != nil {
		return err
	}
	c.settingsApplied = true
	return nil
}

func (c *awgUAPIClient) Device(name string) (*wgtypes.Device, error) {
	if name != c.name {
		return nil, errors.New("wrong awg interface")
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	lines, err := c.exchange("get=1\n")
	if err != nil {
		return nil, err
	}
	result := &wgtypes.Device{Name: name, Type: wgtypes.Userspace}
	var current *wgtypes.Peer
	var sec, nsec int64
	finish := func() {
		if current != nil {
			if sec != 0 || nsec != 0 {
				current.LastHandshakeTime = time.Unix(sec, nsec)
			}
			result.Peers = append(result.Peers, *current)
		}
	}
	for _, line := range lines {
		k, v, ok := strings.Cut(line, "=")
		if !ok {
			return nil, errors.New("invalid awg UAPI line")
		}
		switch k {
		case "private_key":
			raw, err := hex.DecodeString(v)
			if err != nil {
				return nil, errors.New("invalid private key from awg")
			}
			key, err := wgtypes.NewKey(raw)
			if err != nil {
				return nil, err
			}
			result.PrivateKey = key
			result.PublicKey = key.PublicKey()
		case "listen_port":
			n, err := strconv.Atoi(v)
			if err != nil {
				return nil, err
			}
			result.ListenPort = n
		case "fwmark":
			n, err := strconv.Atoi(v)
			if err != nil {
				return nil, err
			}
			result.FirewallMark = n
		case "public_key":
			finish()
			sec, nsec = 0, 0
			raw, err := hex.DecodeString(v)
			if err != nil {
				return nil, errors.New("invalid peer key from awg")
			}
			key, err := wgtypes.NewKey(raw)
			if err != nil {
				return nil, err
			}
			current = &wgtypes.Peer{PublicKey: key}
		case "rx_bytes", "tx_bytes", "last_handshake_time_sec", "last_handshake_time_nsec", "allowed_ip", "endpoint", "preshared_key", "protocol_version":
			if current == nil {
				return nil, errors.New("awg peer field without key")
			}
			switch k {
			case "rx_bytes":
				current.ReceiveBytes, err = strconv.ParseInt(v, 10, 64)
			case "tx_bytes":
				current.TransmitBytes, err = strconv.ParseInt(v, 10, 64)
			case "last_handshake_time_sec":
				sec, err = strconv.ParseInt(v, 10, 64)
			case "last_handshake_time_nsec":
				nsec, err = strconv.ParseInt(v, 10, 64)
			case "allowed_ip":
				var netIP *net.IPNet
				_, netIP, err = net.ParseCIDR(v)
				if err == nil {
					current.AllowedIPs = append(current.AllowedIPs, *netIP)
				}
			case "endpoint":
				current.Endpoint, err = net.ResolveUDPAddr("udp", v)
			case "preshared_key":
				var raw []byte
				raw, err = hex.DecodeString(v)
				if err == nil {
					current.PresharedKey, err = wgtypes.NewKey(raw)
				}
			case "protocol_version":
				current.ProtocolVersion, err = strconv.Atoi(v)
			}
			if err != nil {
				return nil, fmt.Errorf("invalid awg peer %s: %w", k, err)
			}
		}
	}
	finish()
	return result, nil
}

func (*awgUAPIClient) Close() error { return nil }
