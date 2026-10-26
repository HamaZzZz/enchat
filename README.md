```text
  ███████╗███╗   ██╗ ██████╗██╗  ██╗ █████╗ ████████╗
  ██╔════╝████╗  ██║██╔════╝██║  ██║██╔══██╗╚══██╔══╝
  █████╗  ██╔██╗ ██║██║     ███████║███████║   ██║   
  ██╔══╝  ██║╚██╗██║██║     ██╔══██║██╔══██║   ██║   
  ███████╗██║ ╚████║╚██████╗██║  ██║██║  ██║   ██║   
  ╚══════╝╚═╝  ╚═══╝ ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝   ╚═╝
  
  
  EnChat is a secure, open-source, terminal-based End-to-End Encrypted (E2EE) chat application running entirely inside your terminal window as a TUI.

Features
Account-less Identity: Generates local public/private cryptographic keys on first launch.

Deterministic 8-Digit User IDs: Derived automatically from your public key hash (e.g., A3F9B21C).

End-to-End Encryption: Messages are encrypted locally using asymmetric encryption so only the recipient's private key can read them.

Split-Screen TUI: Powered by Python's textual framework with active chat sidebar, real-time message stream, and interactive command bar.

Blind Relay Server: Lightweight WebSocket server routing packets without storing or inspecting message logs.
