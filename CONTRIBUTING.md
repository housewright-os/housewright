# Contributing

Housewright is published as-is from a system that runs one real
household. There is no roadmap and no support commitment. Issues and PRs
are welcome but may sit; forks are encouraged.

## Ground rules

- **DCO sign-off required.** Every commit needs `Signed-off-by:` (use
  `git commit -s`), certifying you have the right to contribute the code
  under the project license.
- **CLA for substantial contributions.** Before a non-trivial PR is
  merged, you will be asked to sign a lightweight contributor license
  agreement granting the maintainer the right to relicense the combined
  work. This is stated up front so it surprises nobody: it preserves the
  project's ability to offer commercial licensing later, which is the
  standard open-core survival path (see Grafana, Nextcloud, Mastodon for
  the license family precedent).
- **Keep the code thin.** The design principle is deterministic plumbing
  plus config, with reasoning delegated to the user's own model. PRs
  that add heavy runtime, vendor lock-in, or cleverness that must chase
  model updates will likely be declined.
- **Safety rails are load-bearing.** Nothing irreversible gets
  automated: no purchases, no switching, no outbound sends, no money
  movement. PRs that weaken a trust rule need an extraordinary case.

## License

AGPL-3.0. If you run a modified version as a network service, you must
offer its source to users. That is a feature, not an accident.
