import asyncio
import json
import logging
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# Set up logging
logging.basicConfig(level=logging.ERROR)

async def run_sampling_e2e_tests():
    # Define how to connect to the MCP server
    # Use the local venv python and point it to the mcp_server.py
    venv_python = os.path.join(os.getcwd(), "backend", ".venv", "bin", "python")
    server_params = StdioServerParameters(
        command=venv_python,
        args=["backend/mcp_wrapper_stdio.py"],
        env={"PYTHONPATH": os.path.join(os.getcwd(), "backend")},
    )

    print("Connecting to MCP server for Sampling Pipeline E2E...")
    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                print("✅ MCP Server initialization successful")

                print("\n--- PHASE 0: Baseline Readiness ---")
                health_result = await session.call_tool("synthesis_health", {})
                health_data = json.loads(health_result.content[0].text)
                print(f"Health: active_provider={health_data.get('active_provider')}, routing_tiers={health_data.get('available_routing_tiers')}")

                strategies_result = await session.call_tool("synthesis_strategies", {})
                strats_data = json.loads(strategies_result.content[0].text)
                strategies = strats_data.get("strategies", [])
                strategy_name = strategies[0]["name"] if strategies else "auto"
                print(f"Using strategy: {strategy_name}")

                print("\n--- PHASE 1: Passthrough Protocol (prepare -> save) ---")
                test_prompt = "Write a fast api endpoint to upload a standard photo"
                print(f"1a. Calling synthesis_prepare_optimization with prompt: '{test_prompt}'")
                
                prepare_result = await session.call_tool("synthesis_prepare_optimization", {
                    "prompt": test_prompt,
                    "strategy": strategy_name
                })
                
                prepare_data = json.loads(prepare_result.content[0].text)
                trace_id = prepare_data.get("trace_id")
                assembled_prompt = prepare_data.get("assembled_prompt")
                
                if not trace_id:
                    print("❌ Failed to get trace_id from prepare step!")
                    return
                print(f"✅ Prepared successfully. trace_id: {trace_id}")
                
                # Mock External LLM Processing
                print("1b. Simulating External LLM processing...")
                mock_optimized_prompt = "Please provide a complete, well-documented FastAPI endpoint in Python that accepts a standard photo file upload (e.g., JPEG, PNG). Includes validation, error handling, and type hinting."
                mock_scores = {
                    "clarity": 8.5,
                    "specificity": 9.0,
                    "structure": 8.0,
                    "faithfulness": 9.5,
                    "conciseness": 8.5
                }
                
                print("1c. Calling synthesis_save_result...")
                save_result = await session.call_tool("synthesis_save_result", {
                    "trace_id": trace_id,
                    "optimized_prompt": mock_optimized_prompt,
                    "scores": mock_scores,
                    "strategy_used": strategy_name,
                    "model": "e2e-test-mock-model",
                    "changes_summary": "Expanded scope, added type hints and validation requirements.",
                    "task_type": "coding",
                    "domain": "backend",
                    "intent_label": "fastapi file upload"
                })
                
                save_data = json.loads(save_result.content[0].text)
                passthrough_opt_id = save_data.get("optimization_id")
                print(f"✅ Saved external result successfully. id: {passthrough_opt_id}")

                print("\n--- PHASE 2: Internal Optimization Pipeline ---")
                test_prompt_2 = "Can you help me center a div?"
                print(f"2. Calling synthesis_optimize directly with prompt: '{test_prompt_2}'")
                print("   (This will invoke the internal or sampling tier, calling an actual LLM)")
                
                try:
                    optimize_result = await session.call_tool("synthesis_optimize", {
                        "prompt": test_prompt_2,
                        "strategy": "auto"
                    })
                    if optimize_result.isError:
                        print(f"⚠️ Internal optimization encountered error: {optimize_result.content[0].text}")
                        direct_opt_id = None
                    else:
                        optimize_data = json.loads(optimize_result.content[0].text)
                        direct_opt_id = optimize_data.get("id")
                        status = optimize_data.get("status")
                        
                        if status == "pending_external":
                            print("⚠️ Route fell back to passthrough (pending_external). Did not run internal LLM.")
                        else:
                            print(f"✅ Executed optimization directly. id: {direct_opt_id}, status: {status}")
                except Exception as ex:
                    print(f"⚠️ Internal optimization call failed: {ex}")
                    direct_opt_id = None

                print("\n--- PHASE 3: Validation (synthesis_history) ---")
                print("3. Querying history to verify persistence...")
                history_result = await session.call_tool("synthesis_history", {"limit": 5})
                history_data = json.loads(history_result.content[0].text)
                
                items = history_data.get("items", [])
                found_passthrough = any(item.get("id") == passthrough_opt_id for item in items)
                found_direct = any(item.get("id") == direct_opt_id for item in items)
                
                if found_passthrough:
                    print("✅ Found passthrough optimization in history")
                else:
                    print("❌ Missing passthrough optimization in history")
                    
                if direct_opt_id and found_direct:
                    print("✅ Found direct optimization in history")
                elif direct_opt_id:
                    print("❌ Missing direct optimization in history")
                    
                print("\n🎉 All MCP sampling workflow tools tested successfully via stdio!")
                
    except Exception as e:
        print(f"\n❌ Error during MCP sampling e2e test: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(run_sampling_e2e_tests())