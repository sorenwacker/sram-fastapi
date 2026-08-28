# Changelog

All notable changes to this project are documented in this file.

## Unreleased - 260828

### Added

- Collaboration management through the SRAM organisation API: provisioning collaborations, inviting users by email with an intended role, promoting and demoting admins, removing members, managing groups and their membership, and connecting or disconnecting this service. Every capability has a control in the demo application at `/collaborations`.
- `SRAMOrganisationClient` in `sram_fastapi.collaborations`, covering the organisation API with typed results and a typed error hierarchy.
- `require_group`, which grants a feature to members of a named group in a named collaboration. Features are mapped in `SRAM_FEATURE_GROUPS` as `feature=collaboration/group`. A feature the deployment does not define is denied to everyone. A feature naming a group without its collaboration is granted only where the organisation API confirms that a service group of that name exists, since a group short name is chosen by whoever creates the group and on its own is not a capability.
- Settings `SRAM_API_BASE_URL`, `SRAM_ORGANISATION_API_TOKEN`, `SRAM_SERVICE_ENTITY_ID`, `COLLABORATION_MANAGER_ENTITLEMENT`, `COLLABORATION_DELETION_ENABLED` and `SRAM_FEATURE_GROUPS`. Each is optional; a missing value disables the part that depends on it and the page names what is missing.
- Documentation: `docs/collaboration-management.md`, and a section in `docs/authorization.md` on granting features through groups.

### Changed

- Deployments default to the SRAM acceptance environment. The organisation API token is an organisation-wide administrator credential, so pointing a deployment at production is a deliberate decision.
- Collaboration deletion is off unless `COLLABORATION_DELETION_ENABLED` is set. It destroys every membership and cannot be undone.
- `docs/sram-setup.md` no longer states that applications cannot manage collaborations. They can, with an organisation API token.

### Fixed

- `make dev` started nothing, because it named an application object that does not exist. It now uses the factory, as the systemd unit already did.
- Values placed in a SRAM API path are percent-encoded, so a uid or identifier carrying path separators cannot re-target a request at another collaboration.
- Group and invitation actions are bound to the collaboration the caller was authorized for, instead of acting on any identifier supplied in the URL.
- Confirmation text moved out of inline event handlers, where HTML escaping does not prevent a name or email from breaking out of a JavaScript string literal.
- SRAM failures and expired sessions are reported as pages instead of surfacing as server errors, and a collaboration that cannot be connected to this service is removed again rather than left unreachable.
