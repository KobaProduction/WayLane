//go:build !linux

package wireguard

import "errors"

type awgRuntime struct{}

func NewAmneziaManager(name string, settings *AWG31) (*Manager, error) {
	return nil, errors.New("AmneziaWG runtime currently requires Linux")
}
func (*awgRuntime) start(netlinkOps) error { return errors.New("AmneziaWG requires Linux") }
func (*awgRuntime) stop() error            { return nil }
