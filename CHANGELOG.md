# Changelog

Notable changes to this repository.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

## [0.3.0] - 2026-10-12

### Added
- Full English translation of the published artefacts

### Changed
- Repository conventions, security documentation and engineering notes are now
  written in English for an international audience

## [0.2.0] - 2026-10-12

### Added
- Layered security gate: local `pre-commit` / `pre-push` hooks plus server-side
  GitHub Actions CI
- `scripts/audit.py`: repository audit covering absolute paths, credential
  files, oversized files and probable third-party content, across both the
  current tree and the full history
- `.gitleaks.toml` and gitleaks integration for secret scanning
- `scripts/install-hooks.sh` to enable the hooks in one step
- `SECURITY.md`, `CONTRIBUTING.md`, `CHANGELOG.md`

### Changed
- The repository root is now a portfolio container; `skill-library-manager`
  moved into a top-level project directory
- `LICENSE` moved to the repository root and applies repository-wide
- Root `README.md` reworked into a project index plus repository conventions

## [0.1.0] - 2026-10-12

### Added
- `skill-library-manager`: a maintenance toolchain for a unified skill library
  shared across 10 AI tools
  - 8 integrity checks and 9 cross-tool integration checks
  - Skill registration with automatic rollback on failure
  - `skill-update-guard`: provenance tracing and safe updates
  - Engineering notes in `docs/ENGINEERING.md`
