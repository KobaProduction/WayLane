package main

// The Agent's OCI trust list is private installation state, not deployment ENV.
// The Panel approves per-node registries and transmits an explicit target on
// the authenticated WSS operation; Node can only forward it over private Unix.
import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
)

const ghcrNodeRepository = "ghcr.io/kobaproduction/waylane-node"

var nodeImageSuffix = regexp.MustCompile(`^sha256:[0-9a-f]{64}$`)
var nodeRepositoryHost = regexp.MustCompile(`^[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[1-9][0-9]{1,4})?$`)
var nodeRepositoryComponent = regexp.MustCompile(`^[a-z0-9]+(?:[._-][a-z0-9]+)*$`)

func validNodeRepository(repo string) bool {
	if repo == "" || len(repo) > 55 || strings.Contains(repo, "@") || strings.Contains(repo, "://") {
		return false
	}
	parts := strings.Split(repo, "/")
	if len(parts) < 2 || !nodeRepositoryHost.MatchString(parts[0]) || !strings.ContainsAny(parts[0], ".:") {
		return false
	}
	if strings.Contains(parts[0], ":") {
		port := strings.Split(parts[0], ":")[1]
		if n, err := strconv.Atoi(port); err != nil || n > 65535 {
			return false
		}
	}
	for _, part := range parts[1:] {
		if !nodeRepositoryComponent.MatchString(part) {
			return false
		}
	}
	return true
}
func imageRepository(image string) string {
	if len(image) > 128 {
		return ""
	}
	repo, digest, found := strings.Cut(image, "@")
	if found && validNodeRepository(repo) && nodeImageSuffix.MatchString(digest) {
		return repo
	}
	return ""
}
func approvedNodeImage(image string) bool { return imageRepository(image) == ghcrNodeRepository }
func approvedNodeImageWithSources(image string, additional []string) bool {
	repo := imageRepository(image)
	if repo == ghcrNodeRepository {
		return true
	}
	for _, allowed := range additional {
		if allowed == repo && repo != "" {
			return true
		}
	}
	return false
}

func (m *manager) approvedImage(image string) bool {
	m.mu.Lock()
	known := append([]string(nil), m.registryRepositories...)
	m.mu.Unlock()
	return approvedNodeImageWithSources(image, known)
}
func (m *manager) registryPath() string {
	return filepath.Join(m.stateDir, "registry-repositories.json")
}
func (m *manager) loadRegistryRepositories() error {
	path := m.registryPath()
	info, err := os.Lstat(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 || info.Size() > 2048 {
		return errors.New("Agent registry trust document must be a private regular file")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return errors.New("Cannot read private Agent registry trust")
	}
	var doc struct {
		Version      int      `json:"version"`
		Repositories []string `json:"repositories"`
	}
	if json.Unmarshal(raw, &doc) != nil || doc.Version != 1 || len(doc.Repositories) > 16 {
		return errors.New("Invalid registry trust document")
	}
	for _, repository := range doc.Repositories {
		if !validNodeRepository(repository) || repository == ghcrNodeRepository {
			return errors.New("Untrusted registry in private document")
		}
	}
	m.registryRepositories = doc.Repositories
	return nil
}
func (m *manager) trustRegistry(repository string) error {
	if repository == ghcrNodeRepository {
		return nil
	}
	if !validNodeRepository(repository) {
		return errors.New("Invalid trusted registry repository")
	}
	for _, known := range m.registryRepositories {
		if known == repository {
			return nil
		}
	}
	if len(m.registryRepositories) >= 16 {
		return errors.New("Too many trusted registry repositories")
	}
	next := append(append([]string{}, m.registryRepositories...), repository)
	raw, err := json.Marshal(struct {
		Version      int      `json:"version"`
		Repositories []string `json:"repositories"`
	}{1, next})
	if err != nil {
		return errors.New("Cannot encode trusted registry")
	}
	file, err := os.CreateTemp(m.stateDir, ".registry-repositories-")
	if err != nil {
		return errors.New("Cannot prepare trusted registry file")
	}
	defer os.Remove(file.Name())
	if err = file.Chmod(0600); err == nil {
		_, err = file.Write(raw)
	}
	if err == nil {
		err = file.Sync()
	}
	if closeErr := file.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		return errors.New("Cannot persist trusted registry")
	}
	if err = os.Rename(file.Name(), m.registryPath()); err != nil {
		return errors.New("Cannot commit trusted registry")
	}
	m.registryRepositories = next
	return nil
}

type registryPullCredential struct {
	Repository string `json:"repository"`
	Username   string `json:"username"`
	Password   string `json:"password"`
}
