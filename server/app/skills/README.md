# Skill Creator

The Skill Creator skill helps the assistant determine if a new skill should be created or an existing one modified. It provides guidance on best practices for skill creation and can check whether skills already exist.

## Usage

The skill supports three actions:

1. **check**: Check if a skill with a given name already exists
2. **suggest**: Get suggestions on best practices for skill creation and modification
3. **create**: Create a skeleton for a new skill

## Parameters

- `skill_name` (string): The name of the skill to check or create
- `skill_description` (string, optional): Description of what the skill should do
- `action` (string): What action to take - "check", "suggest", or "create"

## Examples

### Check if a skill exists
```json
{
  "skill_name": "my_custom_skill",
  "action": "check"
}
```

### Get suggestions for skill creation
```json
{
  "skill_name": "my_custom_skill",
  "skill_description": "Search for information about programming languages",
  "action": "suggest"
}
```

### Create a new skill skeleton
```json
{
  "skill_name": "my_new_skill",
  "skill_description": "Process user data and generate reports",
  "action": "create"
}
```