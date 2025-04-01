import subprocess
import re
import sys

# Get the latest commit message
# commit_message = subprocess.check_output(['git', 'log', '-1', '--pretty=%B']).strip().decode('utf-8')


trigger_payload = <+trigger>
pr_number = <+trigger.prNumber>  # Replace with actual environment variable if needed
commit_message = <+trigger.commitMessage>  # Replace with actual env var if needed

print(f"Commit message: {commit_message}")
print(f"pr_number: {pr_number}")


# Define the regex pattern for the commit message
pattern = r'^(feat|fix|docs|release): [A-Z]+-[0-9]+ .+'

# Check if the commit message matches the pattern
if re.match(pattern, commit_message):
    print("Commit message is valid")
    sys.exit(0)
else:
    print("Invalid commit message. Ensure the message follows the pattern: <type>: <JIRA ID> <message>")
    sys.exit(1)