package main

// Registry auth exists only for the one pull operation. No registry credentials
// are saved to Agent persistent state, passed through process argv or logged.
import (
	"context"
	"errors"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

func pullNodeWithPrivateCredentials(ctx context.Context, image string, credential registryPullCredential) error {
	if credential.Username == "" || credential.Password == "" || imageRepository(image) != credential.Repository {
		return errors.New("Incomplete registry credentials")
	}
	config, err := os.MkdirTemp("/tmp", "waylane-registry-pull-")
	if err != nil {
		return errors.New("Cannot prepare private registry auth")
	}
	defer os.RemoveAll(config)
	if err = os.Chmod(config, 0700); err != nil {
		return errors.New("Cannot protect registry auth")
	}
	host := strings.Split(credential.Repository, "/")[0]
	dockerCmd := func(args ...string) *exec.Cmd {
		cmd := exec.CommandContext(ctx, "docker", append([]string{"--host", "unix://" + dockerSocket}, args...)...)
		cmd.Env = append(os.Environ(), "DOCKER_CONFIG="+filepath.Clean(config))
		cmd.Stdout = io.Discard
		cmd.Stderr = io.Discard
		return cmd
	}
	login := dockerCmd("login", host, "--username", credential.Username, "--password-stdin")
	login.Stdin = strings.NewReader(credential.Password + "\n")
	if login.Run() != nil {
		return errors.New("Private registry authentication rejected")
	}
	if dockerCmd("pull", image).Run() != nil {
		return errors.New("Private registry pull failed")
	}
	return nil
}
