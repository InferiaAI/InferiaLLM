def show_inferia():
    import sys
    import time
    import shutil

    class Colors:
        HEADER = "\033[95m"
        BLUE = "\033[94m"
        CYAN = "\033[96m"
        GREEN = "\033[92m"
        YELLOW = "\033[93m"
        RED = "\033[91m"
        ENDC = "\033[0m"
        BOLD = "\033[1m"
        UNDERLINE = "\033[4m"

    def type_print(text, delay=0.005):
        for char in text:
            sys.stdout.write(char)
            sys.stdout.flush()
            time.sleep(delay)
        print()

    # Get terminal size
    term_width = shutil.get_terminal_size((80, 24)).columns
    separator = "─" * term_width

    def center_text(text):
        path = max(0, (term_width - len(text)) // 2)
        return " " * path + text

    logo_lines = [
        r"██╗███╗   ██╗███████╗███████╗██████╗ ██╗ █████╗      ██╗     ██╗     ███╗   ███╗",
        r"██║████╗  ██║██╔════╝██╔════╝██╔══██╗██║██╔══██╗     ██║     ██║     ████╗ ████║",
        r"██║██╔██╗ ██║█████╗  █████╗  ██████╔╝██║███████║     ██║     ██║     ██╔████╔██║",
        r"██║██║╚██╗██║██╔══╝  ██╔══╝  ██╔══██╗██║██╔══██║     ██║     ██║     ██║╚██╔╝██║",
        r"██║██║ ╚████║██║     ███████╗██║  ██║██║██║  ██║     ███████╗███████╗██║ ╚═╝ ██║",
        r"╚═╝╚═╝  ╚═══╝╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝╚═╝  ╚═╝     ╚══════╝╚══════╝╚═╝     ╚═╝",
    ]

    # Print Header
    print(f"\n{separator}")
    print(center_text(f"{Colors.BOLD}{Colors.CYAN}INFERIA LLM{Colors.ENDC}"))
    print(
        center_text(
            f"{Colors.BLUE}Distributed Inference & Orchestration Operating System{Colors.ENDC}"
        )
    )
    print(f"{separator}\n")

    # Animate the logo
    sys.stdout.write(Colors.GREEN)
    sys.stdout.flush()

    for line in logo_lines:
        padding = max(0, (term_width - len(line)) // 2)
        sys.stdout.write(" " * padding)
        type_print(line, delay=0.004)

    sys.stdout.write(Colors.ENDC)
    sys.stdout.flush()

    print("\n" + separator)
    print("\n")

    desc_title = "InferiaLLM is a operating system for:"
    print(center_text(f"{Colors.BOLD}{desc_title}{Colors.ENDC}"))

    # Center bullet points roughly
    bullets = [
        "• Distributed LLM inference",
        "• Compute orchestration and scheduling",
        "• Guardrails, RBAC, policy, and audit enforcement",
        "• Multi-provider GPU backends (cloud, on-prem, decentralized)",
    ]
    longest_bullet = max(len(b) for b in bullets)
    bullet_padding = max(0, (term_width - longest_bullet) // 2)

    for b in bullets:
        print(" " * bullet_padding + b)

    print(f"\n{separator}")
    print(center_text(f"{Colors.BOLD}{Colors.YELLOW}Quick Start{Colors.ENDC}"))
    print(f"{separator}\n")

    print(f"  {Colors.GREEN}inferiallm init{Colors.ENDC}")
    print("      Initialize databases, roles, and control-plane schemas\n")

    print(f"  {Colors.GREEN}inferiallm migrate{Colors.ENDC}")
    print("      Apply database schema migrations\n")

    print(f"  {Colors.GREEN}inferiallm start{Colors.ENDC}")
    print("      Start all gateways, workers, and sidecars")

    print(f"\n{separator}")
    print(center_text(f"{Colors.BOLD}{Colors.YELLOW}Core Commands{Colors.ENDC}"))
    print(f"{separator}\n")

    cols = [
        (
            f"{Colors.CYAN}init{Colors.ENDC}",
            "Initialize Inferia databases and bootstrap state",
        ),
        (
            f"{Colors.CYAN}start all{Colors.ENDC}",
            "Start all services (orchestration, inference, api-gateway)",
        ),
        (
            f"{Colors.CYAN}start orchestration{Colors.ENDC}",
            "Run orchestration API + worker + sidecars",
        ),
        (f"{Colors.CYAN}start inference{Colors.ENDC}", "Run inference gateway"),
        (
            f"{Colors.CYAN}start api-gateway{Colors.ENDC}",
            "Run API Gateway (auth, RBAC, policy, proxy)",
        ),
    ]

    for cmd, desc in cols:
        print(f"  {cmd}")
        print(f"      {desc}\n")

    print(f"{separator}")
    print(center_text(f"{Colors.BOLD}{Colors.YELLOW}Documentation{Colors.ENDC}"))
    print(f"{separator}\n")

    print(f"  {Colors.BOLD}Docs:{Colors.ENDC}")
    print("    inferia/README.md\n")

    print(f"  {Colors.BOLD}Online Docs:{Colors.ENDC}")
    print("    https://docs.inferia.ai/docs\n")

    print(f"  {Colors.BOLD}GitHub Repo:{Colors.ENDC}")
    print("    https://github.com/InferiaAI/InferiaLLM")

    print(f"\n{separator}\n")


def show_orchestration_docs():
    print(r"""
──────────────────────────────────────────────────────────────────────────────
                 INFERIALLM · ORCHESTRATION GATEWAY
──────────────────────────────────────────────────────────────────────────────

The Orchestration Gateway is the brain of Compute Orchestration.

Responsibilities:
• Compute lifecycle management
• Workload scheduling
• GPU pool orchestration
• Autoscaling decisions
• Provider abstraction (AWS, GCP, On-Prem, Nosana)

Components started by this command:
• Orchestration API
• Background Worker
• Sidecars (e.g., Nosana)

Usage:
  inferiallm start orchestration

Environment Requirements:
• PostgreSQL (control-plane DB)
• Redis (state + queues)
• Provider credentials (optional)

Docs:
    inferia/gateways/orchestration_gateway/README.md
          
Online Docs:
   https://docs.inferia.ai/docs/gateways/orchestration_gateway/orchestration

GitHub Repos:
    https://github.com/InferiaAI/InferiaLLM/apps/orchestration-gateway
    https://github.com/InferiaAI/InferiaLLM/services/orchestration

──────────────────────────────────────────────────────────────────────────────
""")


def show_inference_docs():
    print(r"""
──────────────────────────────────────────────────────────────────────────────
                   INFERIALLM · INFERENCE GATEWAY
──────────────────────────────────────────────────────────────────────────────

The Inference Gateway exposes LLM inference endpoints.

Responsibilities:
• Model routing
• Request validation
• Streaming / batching
• Provider abstraction (vLLM, TGI, Python)
• Policy enforcement (via API Gateway)

Usage:
  inferiallm start inference

Typical APIs:
• /v1/chat/completions
• /v1/embeddings
• /health

Docs:
    inferia/gateways/inference_gateway/README.md
          
Online Docs:
    https://docs.inferia.ai/docs/gateways/inference_gateway/inference

GitHub Repo:
    https://github.com/InferiaAI/InferiaLLM/apps/inference-gateway
──────────────────────────────────────────────────────────────────────────────
""")


def show_api_gateway_docs():
    print(r"""
──────────────────────────────────────────────────────────────────────────────
                  INFERIALLM · API GATEWAY
──────────────────────────────────────────────────────────────────────────────

The API Gateway is the central entry point for dashboard and management operations.

Responsibilities:
• Authentication & Authorization (JWT, RBAC)
• Request routing & proxying to downstream services
• Rate limiting & quotas
• Audit logging
• Prompt templates
• Management APIs (organizations, users, deployments, API keys)

Usage:
  inferiallm start api-gateway

Proxy Routes:
• /api/v1/deployments/* → Orchestration Service
• /api/v1/pools/* → Orchestration Service
• /auth/* → Authentication handlers
• /management/* → Management operations
• /admin/* → RBAC administration

Docs:
  inferia/services/api_gateway/README.md
          
Online Docs:
  https://docs.inferia.ai/docs/services/api_gateway
        
GitHub Repo:
  https://github.com/InferiaAI/InferiaLLM/services/api_gateway
──────────────────────────────────────────────────────────────────────────────
""")
