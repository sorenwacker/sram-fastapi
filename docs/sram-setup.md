# SRAM Setup Guide

This guide covers registering your application as a service in SRAM and connecting it to collaborations.

## Prerequisites

- An account at a Dutch research institution (or eduID)
- Admin access to a SRAM collaboration (or ability to create one)

## Step 1: Register Your Service with SRAM

Services must be registered in SRAM before they can be connected to collaborations. SRAM provides a self-service registration form.

### Submit a Service Registration Request

1. Go to the [SRAM Service Registration Form](https://sram.surf.nl/new-service-request)
2. Log in with your institutional account
3. Fill in the required information:

| Field | Description | Example |
|-------|-------------|---------|
| Service name | Display name for your application | TU Delft SRAM Demo |
| Service description | Brief description of what the service does | Demo application for SRAM authentication |
| Organization | Your institution | Delft University of Technology |
| Protocol | Authentication protocol (see below) | OIDC |
| Redirect URI | OAuth callback URL | `https://your-app.example.com/auth/callback` |
| Privacy Policy URL | Link to your privacy policy | `https://your-app.example.com/privacy` |
| AUP URL | Acceptable Use Policy URL | `https://your-app.example.com/aup` |
| Contact email | Technical contact | your-email@tudelft.nl |

4. Submit the request

SURF will review your request and contact you if clarification is needed. For questions, contact sram-support@surf.nl.

### Testing environment

SURF provides a testing environment for SRAM.
In this environment you can create test accounts and play with the application.
You'll likely have to ask `ICT-Servicedesk@tudelft.nl` to enable it for your account.

1. Register your application in the acceptance environment: [acc.sram.surf.nl/new-service-request](https://acc.sram.surf.nl/new-service-request)
2. Create an application account and as many test accounts as needed in the eduID test enviroment: [https://test.eduid.nl/home](https://test.eduid.nl/home)
    - Tip: you can use "+" to create unlimited emails, e.g. `my_netid+test1@tudelft.nl`, `my_netid+test2@tudelft.nl`

### Authentication Protocol Options

**Web-based applications:**

- **OpenID Connect (OIDC)**: Recommended for web applications. Provides user attributes and group memberships via browser-based flow.
- **SAML 2.0**: Alternative browser-based protocol with equivalent functionality.

**Command-line applications:**

- **SSH public keys**: For terminal access. Provides username only; requires pre-provisioning of keys.
- **PAM web login**: Brings federated authentication to terminal-based login.

### Provisioning Options

If you need user data before first login:

- **LDAP**: Traditional protocol for pre-authentication user provisioning.
- **SCIM**: Modern API for bidirectional user data synchronization.

### After Approval

Once approved, you will receive:

- **Client ID**: OIDC client identifier
- **Client Secret**: OIDC client secret (keep this secure)

## Step 2: Create or Join a Collaboration

Access to services in SRAM is granted through collaboration membership. Users must be members of a collaboration that is connected to your service.

### Option A: Create a New Collaboration

1. Go to [sram.surf.nl](https://sram.surf.nl)
2. Log in with your institutional account
3. Navigate to **Collaborations**
4. Click **New collaboration** or **Request collaboration**
5. Fill in the required fields:
   - **Name**: Descriptive name (e.g., "SRAM Demo Users")
   - **Short name**: Abbreviation used in identifiers
   - **Description**: Purpose of the collaboration
6. Submit the request

Note: Some institutions require approval before you can create collaborations. If you don't see a create button, contact your institution's SRAM administrator.

### Option B: Use an Existing Collaboration

If you're already a member or admin of a collaboration, you can connect your service to it (see Step 3).

## Step 3: Connect Service to Collaboration

Once your service is registered and you have a collaboration:

### If You're the Service Admin

1. Go to [sram.surf.nl](https://sram.surf.nl)
2. Navigate to **Services** in the main menu
3. Find your service
4. Click **Connect to collaboration**
5. Select the collaboration(s) to connect

### If You're the Collaboration Admin

1. Go to your collaboration in SRAM
2. Navigate to the **Services** tab
3. Click **Add service** or **Connect service**
4. Select your registered service from the list
5. Confirm the connection

Note: The service must be registered first (Step 1) before it appears in the list.

## Step 4: Add Members

Members of the connected collaboration will have access to your service.

1. Go to your collaboration in SRAM
2. Navigate to **Members**
3. Click **Invite member**
4. Enter their email address
5. They will receive an invitation email

Members can be from:

- Any Dutch research institution (via SURFconext)
- External organizations (via eduID guest accounts)

## Step 5: Configure Your Application

Add the OIDC credentials to your application:

```bash
# .env file
SRAM_OIDC_CLIENT_ID=your-client-id
SRAM_OIDC_CLIENT_SECRET=your-client-secret
SRAM_OIDC_DISCOVERY_URL=https://proxy.sram.surf.nl/.well-known/openid-configuration
BASE_URL=https://your-app.example.com
SECRET_KEY=generate-a-secure-random-key
```

Generate a secure secret key:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## Step 6: Enable Application Token Support (Optional)

SRAM supports two authentication methods: browser-based OIDC login and application tokens for CLI/API access.

### Understanding SRAM Tokens

There are two distinct types of tokens involved in application token authentication:

#### User Application Tokens

**What they are:** Personal access tokens that users create in the SRAM portal to authenticate with your application without browser login.

**Who creates them:** End users, through the SRAM portal.

**What they're used for:** CLI tools, scripts, API clients, automated workflows - any scenario where browser-based login isn't practical.

**How users create them:**
1. User logs into SRAM portal
2. Navigates to their collaboration
3. Finds "Tokens" or "Application tokens" section
4. Selects your application and creates a token
5. Copies the token for use in their scripts/tools

**How users use them:**
```bash
curl -H "Authorization: Bearer <user-application-token>" https://your-app/api/endpoint
```

**Properties:**
- Tied to a specific user and application
- Have configurable expiration (days to months)
- Can be revoked by the user or admin
- Inherit the user's group memberships and permissions

#### Service Introspection Token

**What it is:** A server-side credential that your application uses to validate user tokens with SRAM.

**Who creates it:** Service administrators, through SRAM service configuration.

**What it's used for:** Your application uses this token to ask SRAM "is this user's token valid, and who does it belong to?"

**How it works:**
```
User                          Your App                         SRAM
  |                              |                               |
  |-- Bearer <user-token> ------>|                               |
  |                              |                               |
  |                              |-- POST /api/tokens/introspect |
  |                              |   Authorization: Bearer       |
  |                              |     <service-introspection-   |
  |                              |      token>                   |
  |                              |   Body: token=<user-token>    |
  |                              |                               |
  |                              |<-- 200 OK -------------------|
  |                              |   {                           |
  |                              |     "active": true,           |
  |                              |     "user": { ... }           |
  |                              |   }                           |
  |                              |                               |
  |<-- 200 OK ------------------|                               |
  |   {"message": "Hello!"}     |                               |
```

**Properties:**
- Server-side secret - users never see this token
- One per service/application
- Must be kept secure (environment variable, secrets manager)
- If compromised or expired, all token validation fails

#### Key Differences

| Aspect | User Application Token | Service Introspection Token |
|--------|----------------------|----------------------------|
| Created by | End users | Service administrators |
| Stored in | User's password manager, scripts | Server environment variables |
| Purpose | Authenticate API requests | Validate user tokens |
| Visibility | User knows their own token | Users never see this |
| Scope | One user, one application | All users of the service |
| If compromised | Revoke and create new one | Update server config immediately |

### Get the Service Introspection Token

1. Go to [sram.surf.nl](https://sram.surf.nl)
2. Navigate to **Services** and find your service
3. Look for **Token introspection** or **API settings**
4. Copy the introspection token (this is a service-level credential)

### Configure Your Application

Add the introspection token to your environment:

```bash
# .env file (add to existing config)
SRAM_INTROSPECTION_TOKEN=your-service-introspection-token
SRAM_INTROSPECTION_URL=https://sram.surf.nl/api/tokens/introspect
```

### How Users Create Application Tokens

Users create their own tokens through the SRAM portal:

1. Go to [sram.surf.nl](https://sram.surf.nl)
2. Navigate to the collaboration they're a member of
3. Find **Tokens** or **Application tokens** section
4. Select your application from the list
5. Click **Create token**
6. Copy the generated token

Users then include this token in API requests:

```bash
curl -H "Authorization: Bearer USER_TOKEN" https://your-app.example.com/api/endpoint
```

### Token Introspection Response

When validating a user token, SRAM returns:

```json
{
  "active": true,
  "status": "token-valid",
  "sub": "user-id@sram.surf.nl",
  "user": {
    "name": "User Name",
    "email": "user@institution.nl",
    "username": "username",
    "eduperson_entitlement": ["urn:mace:surf.nl:sram:group:..."],
    "voperson_external_affiliation": "employee@institution.nl"
  }
}
```

Possible status values:

| Status | Meaning |
|--------|---------|
| `token-valid` | Token is active and valid |
| `token-unknown` | Token not recognized (wrong service or invalid) |
| `token-expired` | Token has expired |
| `user-suspended` | User account is suspended |
| `token-not-connected` | Service not connected to user's collaboration |

## SRAM API Capabilities and Limitations

SRAM provides APIs for authentication and token validation, but has limited support for programmatic management.

### What Applications Can Do

| Capability | Supported | Method |
|------------|-----------|--------|
| Authenticate users (browser) | Yes | OIDC/SAML |
| Validate user tokens | Yes | Token introspection API |
| Read user attributes | Yes | From token/introspection response |
| Read group memberships | Yes | Via `eduperson_entitlement` claim |

### What Applications Cannot Do

| Capability | Supported | Alternative |
|------------|-----------|-------------|
| Create groups | No | Collaboration admins via SRAM portal |
| Delete groups | No | Collaboration admins via SRAM portal |
| Add users to groups | No | Collaboration admins invite users |
| Remove users from groups | No | Collaboration admins via SRAM portal |
| Manage collaborations | No | Via SRAM portal only |

### Why These Limitations Exist

SRAM is designed as a central identity and access management system where:

- **Human administrators** control access decisions
- **Audit trails** track who granted access and when
- **Institutional policies** govern collaboration membership

This separation of concerns ensures that applications cannot unilaterally grant or revoke access.

### For Advanced Use Cases

If you need programmatic group management, contact SURF:

- Email: sram-support@surf.nl
- Documentation: [SRAM Wiki](https://wiki.surfnet.nl/display/SRAM)

## Troubleshooting

### "Unknown Client ID" Error

The client ID in your configuration doesn't match any registered service. Verify:

- The client ID is correct (no typos or extra whitespace)
- The service is registered in SRAM (contact sram-support@surf.nl)

### "No access to application" Error

The user authenticated successfully but isn't authorized. This means:

- The service is not connected to any collaboration the user belongs to
- Or the user is not a member of a connected collaboration

Solution: Connect the service to a collaboration and ensure the user is a member.

### Can't Find "Services" Tab in Collaboration

Some collaborations inherit services from a parent collaboration. You may need to:

- Check the parent collaboration
- Register your service with SRAM first (Step 1)
- Contact your collaboration admin

## References

- [SRAM Service Registration Form](https://sram.surf.nl/new-service-request)
- [Connect an application to SRAM](https://servicedesk.surf.nl/wiki/spaces/IAM/pages/74226152/Connect+an+application+to+SRAM)
- [SRAM Attributes Documentation](https://servicedesk.surf.nl/wiki/spaces/IAM/pages/74226142/Attributes+in+SRAM)
- [SRAM Documentation](https://servicedesk.surf.nl/wiki/spaces/IAM/pages/74226073/SURF+Research+Access+Management)
- [SURF Research Access Management](https://www.surf.nl/en/services/identity-access-management/surf-research-access-management)
- SRAM Support: sram-support@surf.nl
