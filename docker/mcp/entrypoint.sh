#!/bin/sh
# Copy SSH key for CLI agent chat (must run as root to read the bind-mounted key)
if [ -f /run/secrets/team_ssh_key ]; then
    cp /run/secrets/team_ssh_key /app/.ssh/team_key
    chown mcp:mcp /app/.ssh/team_key
    chmod 600 /app/.ssh/team_key
fi

# Drop to mcp user and exec the CMD — gosu replaces PID 1 so signals propagate
exec gosu mcp "$@"
