# SRAM Setup Guide

This guide covers registering your application as a service in SRAM and connecting it to collaborations.

## Prerequisites

- An account at a Dutch research institution (or eduID)
- Admin access to a SRAM collaboration (or ability to create one)

## Step 1: Register Your Service with SRAM

Services must be registered in SRAM before they can be connected to collaborations.

### Contact SRAM Support

Email **sram-support@surf.nl** with the following information:

| Field | Description | Example |
|-------|-------------|---------|
| Service name | Display name for your application | TU Delft SRAM Demo |
| Service description | Brief description of what the service does | Demo application for SRAM authentication |
| Organization | Your institution | Delft University of Technology |
| Protocol | Authentication protocol | OIDC |
| Redirect URI | OAuth callback URL | `https://your-app.example.com/auth/callback` |
| Privacy Policy URL | Link to your privacy policy | `https://your-app.example.com/privacy` |
| AUP URL | Acceptable Use Policy URL | `https://your-app.example.com/aup` |
| Contact email | Technical contact | your-email@tudelft.nl |

SURF will register the service and provide you with:

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

- [SRAM Documentation](https://servicedesk.surf.nl/wiki/spaces/IAM/pages/74226073/SURF+Research+Access+Management)
- [Connect a service to SRAM](https://wiki.surfnet.nl/display/SRAM/Connect+a+service+to+your+CO)
- [SRAM for service providers](https://servicedesk.surf.nl/wiki/pages/viewpage.action?pageId=74226770)
- SRAM Support: sram-support@surf.nl
