package main

// Restrict updates to operator-approved OCI image repositories and immutable
// digests. A custom repository never disables the pinned official GHCR source.
// The Backend mirrors these rules before issuing an authenticated WSS action.
import (
	"errors"
	"os"
	"regexp"
	"strings"
)

const ghcrNodeRepository = "ghcr.io/kobaproduction/waylane-node"
const extraNodeRepositoryEnv = "WAYLANE_NODE_EXTRA_IMAGE_REPOSITORY"

var nodeImageSuffix = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
var nodeRepositoryHost = regexp.MustCompile(`^[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[1-9][0-9]{1,4})?$`)
var nodeRepositoryComponent = regexp.MustCompile(`^[a-z0-9]+(?:[._-][a-z0-9]+)*$`)

func extraNodeRepository() (string, error) {
	repo := strings.TrimSpace(os.Getenv(extraNodeRepositoryEnv))
	if repo == "" {
		return "", nil
	}
	if len(repo) > 55 || strings.Contains(repo, "@") || strings.Contains(repo, "://") {
		return "", errors.New("invalid extra Node OCI repository (max 55 bytes)")
	}
	parts := strings.Split(repo, "/")
	if len(parts) < 2 || !nodeRepositoryHost.MatchString(parts[0]) ||
		(!strings.ContainsAny(parts[0], ".:")) {
		return "", errors.New("invalid extra Node OCI repository host")
	}
	for _, part := range parts[1:] {
		if !nodeRepositoryComponent.MatchString(part) {
			return "", errors.New("invalid extra Node OCI repository path")
		}
	}
	return repo, nil
}

func approvedNodeImage(image string) bool {
	repo, digest, found := strings.Cut(image, "@")
	if !found || len(image) > 128 || !nodeImageSuffix.MatchString(digest) {
		return false
	}
	if repo == ghcrNodeRepository {
		return true
	}
	extra, err := extraNodeRepository()
	return err == nil && extra != "" && repo == extra
}
