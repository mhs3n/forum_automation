"""Example demonstrating the ComputerAgent with Claude 4.5 via Cua."""

import asyncio
import logging
import os
import traceback
import signal

from computer import Computer, VMProviderType

# Import the unified agent class and types
from agent import ComputerAgent

# Import utility functions
from utils import load_dotenv_files, handle_sigint

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def validate_required_env_vars(model_name: str, provider: str):
    """Validate that all required environment variables are present.

    Raises:
        ValueError: If any required environment variables are missing.
    """
    required_keys = []

    if provider.lower() == "cloud":
        required_keys += ["CUA_API_KEY", "CUA_SANDBOX_NAME"]
    elif provider.lower() == "docker":
        # Name is optional for docker; API key not needed
        pass
    elif provider.lower() == "host":
        # Using a running host computer server; nothing required beyond optional host/port
        pass

    # Anthropic BYOK models need the provider key, Cua-routed models do not.
    if model_name.startswith("anthropic/") or model_name.startswith("omniparser+anthropic/"):
        required_keys.append("ANTHROPIC_API_KEY")

    missing_keys = [key for key in required_keys if not os.getenv(key)]

    if missing_keys:
        raise ValueError(
            f"Missing required environment variables: {', '.join(missing_keys)}. "
            f"Please check your .env file and ensure all keys from .env.example are set."
        )


async def run_agent_example(model_name: str):
    """Run example of using the ComputerAgent with different models."""
    print("\n=== Example: ComputerAgent with Claude 4.5 models ===")
    print(f"Using model: {model_name}")

    try:
        provider = os.getenv("CUA_PROVIDER", "cloud").lower()

        # Create a remote Linux computer with Cua
        if provider == "docker":
            novnc_port = int(os.getenv("CUA_DOCKER_NOVNC_PORT", "8006"))
            api_port = int(os.getenv("CUA_DOCKER_PORT", "7777"))
            computer = Computer(
                os_type="linux",
                provider_type=VMProviderType.DOCKER,
                image=os.getenv("CUA_DOCKER_IMAGE", "trycua/cua-xfce:latest"),
                host=os.getenv("CUA_DOCKER_HOST", "localhost"),
                port=api_port,
                name=os.getenv("CUA_SANDBOX_NAME", "cua-docker-sandbox"),
                noVNC_port=novnc_port,
            )
        elif provider == "host":
            computer = Computer(
                os_type="linux",
                use_host_computer_server=True,
                host=os.getenv("CUA_HOST", "localhost"),
                port=int(os.getenv("CUA_PORT", "7777")),
            )
        else:
            computer = Computer(
                os_type="linux",
                api_key=os.getenv("CUA_API_KEY"),
                name=os.getenv("CUA_SANDBOX_NAME"),
                provider_type=VMProviderType.CLOUD,
            )

        # OpenAI computer-use model (kept for reference)
        # openai_model = "openai/computer-use-preview"

        # Claude 4.5 models via Cua VLM Router or Anthropic BYOK.
        # Other options include:
        #   - "cua/anthropic/claude-opus-4.5"  # higher reasoning, higher cost
        #   - "anthropic/claude-opus-4-20250514"  # BYOK; requires ANTHROPIC_API_KEY
        agent = ComputerAgent(
            model=model_name,
            tools=[computer],
            only_n_most_recent_images=3,
            verbosity=logging.DEBUG,
            trajectory_dir="trajectories",
            use_prompt_caching=True,
            max_trajectory_budget=1.0,
        )

        # Example tasks to demonstrate the agent
        tasks = [
            "Open the webpage: https://datalytics.drg-pepp.de/drg-pepp-grouper/grouping",
            "If a login prompt appears, use user 'Medmatic' and password 'jG82YJ1uRB7obBjNXt7mQYQk8'.",
            "Wait until the DRG/PEPP grouper form is fully visible.",
            "Fill the form with the following values:",
            "• Gender: M",
            "• Age: 89",
            "• Age type: J",
            "• Admission weight: 0",
            "• Ventilation time: 0",
            "• Length of stay: 6",
            "• Department type: 1",
            "• Admission type: 01",
            "• Admission reason: N",
            "• Discharge type: 01",
            "• DRG version: 76",
            "• Base rate: 4502.67",
            "• Diagnoses (enter all): K25.0, D69.58, D68.35, D62, E11.90, I10.00, K31.81, Z86.7, Z92.1",
            "• Procedures (enter all): 8-800.c0, 8-930, 1-632.0, 1-440.a",
            "• Procedure dates: 02.12.2025, 02.12.2025, 03.12.2025, 03.12.2025 (align each date with its procedure if the UI requires).",
            "Ensure each field is correctly populated; use dropdowns if available.",
            "Submit/start the grouping process.",
            "Wait for results to load and confirm the DRG output is visible."
        ]


        # Use message-based conversation history
        history = []
        
        for i, task in enumerate(tasks):
            print(f"\nExecuting task {i+1}/{len(tasks)}: {task}")
            
            # Add user message to history
            history.append({"role": "user", "content": task})
            
            # Run agent with conversation history
            async for result in agent.run(history, stream=False):
                # Add agent outputs to history
                history += result.get("output", [])
                
                # Print output for debugging
                for item in result.get("output", []):
                    if item.get("type") == "message":
                        content = item.get("content", [])
                        for content_part in content:
                            if content_part.get("text"):
                                print(f"Agent: {content_part.get('text')}")
                    elif item.get("type") == "computer_call":
                        action = item.get("action", {})
                        action_type = action.get("type", "")
                        print(f"Computer Action: {action_type}({action})")
                    elif item.get("type") == "computer_call_output":
                        print("Computer Output: [Screenshot/Result]")
                        
            print(f"✅ Task {i+1}/{len(tasks)} completed: {task}")

    except Exception as e:
        logger.error(f"Error in run_agent_example: {e}")
        traceback.print_exc()
        raise


def main():
    """Run the ComputerAgent example with Claude 4.5 and Cloud VM."""
    try:
        load_dotenv_files()

        model_name = os.getenv("CUA_MODEL", "cua/anthropic/claude-sonnet-4.5")
        provider = os.getenv("CUA_PROVIDER", "cloud")

        # Validate all required environment variables are present
        validate_required_env_vars(model_name, provider)

        # Register signal handler for graceful exit
        signal.signal(signal.SIGINT, handle_sigint)

        asyncio.run(run_agent_example(model_name))
    except Exception as e:
        print(f"Error running example: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()
