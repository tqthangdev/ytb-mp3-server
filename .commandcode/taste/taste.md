# Taste

## Communication
- Prefers to communicate in Vietnamese; expects assistant responses in Vietnamese. Confidence: 0.9

## Tooling & Stack
- Prefers Python for backend services (explicitly requested migrating an existing Node.js server to Python). Confidence: 0.6

## Workflow
- Prefers to run/manual-test changes themselves after the assistant prepares things — asks for a UI ↔ server mapping and handoff summary with run instructions instead of the assistant doing all the testing. Confidence: 0.6
- Expects the assistant to coordinate changes across all related repos of a project (e.g., after migrating/porting the server, also update the client's config that points to the server) rather than leaving stale URLs/config behind; explicitly asks for the client to be re-pointed to the new server. Confidence: 0.7
- Values project documentation — requests a README file for their projects/servers covering setup, run instructions, API, and architecture. Confidence: 0.5
