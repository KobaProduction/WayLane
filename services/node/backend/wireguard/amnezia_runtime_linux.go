//go:build linux

package wireguard

import (
	"errors"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"regexp"
	"syscall"
	"time"

	"github.com/vishvananda/netlink"
)

var validAmneziaInterface = regexp.MustCompile(`^[a-zA-Z0-9_.-]{1,15}$`)

type awgRuntime struct {
	cmd  *exec.Cmd
	name string
}

func NewAmneziaManager(name string, settings *AWG31) (*Manager, error) {
	if !validAmneziaInterface.MatchString(name) {
		return nil, errors.New("invalid AmneziaWG interface name")
	}
	if err := settings.Validate(); err != nil {
		return nil, err
	}
	return &Manager{
		client:    &awgUAPIClient{name: name, settings: settings},
		iFaceName: name,
		nl:        defaultNetlinkOps{},
		configure: defaultConfigureDevice,
		amnezia:   &awgRuntime{name: name},
	}, nil
}

func (a *awgRuntime) start(nl netlinkOps) error {
	// Never delete or take over an unrelated kernel WireGuard/TUN interface.
	if _, err := nl.LinkByName(a.name); err == nil {
		return fmt.Errorf("amnezia interface %s already exists", a.name)
	} else {
		var missing netlink.LinkNotFoundError
		if !errors.As(err, &missing) {
			return fmt.Errorf("checking amnezia interface: %w", err)
		}
	}
	if _, err := os.Stat(amneziaSocketDir + "/" + a.name + ".sock"); err == nil {
		return errors.New("amnezia UAPI socket already exists")
	} else if !os.IsNotExist(err) {
		return err
	}
	cmd := exec.Command("/usr/local/bin/amneziawg-go", "-f", a.name)
	cmd.Stdout = io.Discard
	cmd.Stderr = io.Discard
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("starting AmneziaWG-Go: %w", err)
	}
	a.cmd = cmd
	readyUntil := time.Now().Add(8 * time.Second)
	for time.Now().Before(readyUntil) {
		conn, err := net.DialTimeout("unix", amneziaSocketDir+"/"+a.name+".sock", 150*time.Millisecond)
		if err == nil {
			conn.Close()
			if _, err = nl.LinkByName(a.name); err == nil {
				return nil
			}
		}
		time.Sleep(100 * time.Millisecond)
	}
	_ = a.stop()
	return errors.New("AmneziaWG-Go failed to create its interface and UAPI socket")
}
func (a *awgRuntime) stop() error {
	if a.cmd == nil || a.cmd.Process == nil {
		return nil
	}
	proc := a.cmd.Process
	a.cmd = nil
	_ = proc.Signal(syscall.SIGTERM)
	done := make(chan error, 1)
	go func() { _, err := proc.Wait(); done <- err }()
	select {
	case <-done:
		return nil
	case <-time.After(3 * time.Second):
		_ = proc.Kill()
		<-done
		return errors.New("AmneziaWG-Go required forceful shutdown")
	}
}
