
import asyncio
from models import Message, PromptProcessRequest
from gateway.router import process_prompt
import json

# MOCK Prompt Engine to assume behavior
from prompt.engine import prompt_engine
# Monkey patch assemble_context
async def mock_assemble_context(query, collection, top_k):
    if collection == "docs-v1":
        return f"RAG RESULT for {query} from docs-v1"
    return ""
prompt_engine.assemble_context = mock_assemble_context

# Mock fetch template (normally hits DB, we simulate successful fetch)
def mock_process_prompt(template_id, variables):
    # Simulate DB fetch of "customer-support" -> "System: Hello {{ user_name }}, context: {{ context }}"
    if template_id == "customer-support":
        return f"System: Hello {variables.get('user_name', 'UNKNOWN')}, context: {variables.get('context', 'EMPTY')}"
    return f"Generic Template {template_id}"
prompt_engine.process_prompt = mock_process_prompt

async def verify_advanced_linking():
    print("Starting Advanced Linking Verification...")
    
    # CASE 1: Full Variable Mapping
    # Template: "customer-support" (simulated above)
    # Mapping: 
    #   user_name -> request: "user"
    #   context -> rag: "docs-v1"
    
    req = PromptProcessRequest(
        model="dummy-model",
        messages=[Message(role="user", content="help me")],
        template_vars={"user": "Alice", "other": "ignored"},
        template_config={
            "enabled": True,
            "base_template_id": "customer-support",
            "variable_mapping": {
                "user_name": {"source": "request", "key": "user"},
                "context": {"source": "rag", "collection_id": "docs-v1"}
            }
        }
    )
    
    resp = await process_prompt(req)
    
    system_msg = next((m for m in resp.messages if m.role == "system"), None)
    if system_msg:
        print(f"System Message: {system_msg.content}")
        # Expected: "System: Hello Alice, context: RAG RESULT for help me from docs-v1"
        if "Hello Alice" in system_msg.content and "RAG RESULT" in system_msg.content:
             print("PASS: Variable Mapping (Request + RAG) worked")
        else:
             print("FAIL: Content mismatch")
    else:
        print("FAIL: No system message generated")

    # CASE 2: Static Value + Legacy RAG Fallback
    # Mapping: tone -> static: "friendly"
    # Legacy: RAG enabled but not mapped
    
    # Mock template with override
    req2 = PromptProcessRequest(
        model="dummy-model",
        messages=[Message(role="user", content="help me")],
        template_config={
            "enabled": True,
            "content": "Be {{ tone }}. Context: {{ context }}",
            "variable_mapping": {
                "tone": {"source": "static", "value": "friendly"}
            }
        },
        rag_config={"enabled": True, "default_collection": "docs-v1"}
    )
    
    resp2 = await process_prompt(req2)
    system_msg2 = next((m for m in resp2.messages if m.role == "system"), None)
    
    if system_msg2:
        print(f"System Message 2: {system_msg2.content}")
        # Expected: "Be friendly. Context: RAG RESULT..."
        if "Be friendly" in system_msg2.content and "RAG RESULT" in system_msg2.content:
             print("PASS: Static Value + Legacy RAG Fallback worked")
        else:
             print("FAIL: Content mismatch")
    else:
        print("FAIL: No system message generated")

if __name__ == "__main__":
    asyncio.run(verify_advanced_linking())
