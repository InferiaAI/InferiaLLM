
import asyncio
import httpx
import json

BASE_URL = "http://localhost:8000"

async def verify_prompt_process():
    print("Testing /internal/prompt/process...")
    
    # Define a test case
    payload = {
        "messages": [
            {"role": "user", "content": "How's the weather?"}
        ],
        "model": "test-model",
        "rag_config": {
            "enabled": True,
            "default_collection": "test_collection",
            "top_k": 3
        },
        "template_id": "test-template",
        "template_vars": {"user_name": "Alice"}
    }
    
    # Note: We need a template to exist for 'test-template' ID if we want to test template lookup.
    # But wait, our implementation checks DB.
    # To avoid DB dependency in this quick verify, we using 'template_content' Override?
    # Yes, let's use dynamic content.
    
    payload["template_content"] = "System: Be helpful to {{ user_name }}. Context: {{ context }}"
    
    headers = {
        "X-Internal-API-Key": "dev-internal-key-change-in-prod",
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                f"{BASE_URL}/internal/prompt/process", 
                json=payload, 
                headers=headers
            )
            resp.raise_for_status()
            data = resp.json()
            
            print("Response Status:", resp.status_code)
            print("Response JSON:", json.dumps(data, indent=2))
            
            # Validation
            messages = data.get("messages", [])
            if not messages:
                print("FAIL: No messages returned")
                return
            
            # Check for System message
            sys_msg = next((m for m in messages if m["role"] == "system"), None)
            if sys_msg:
                print(f"PASS: System message found: {sys_msg['content']}")
                if "Alice" in sys_msg["content"]:
                    print("PASS: Variable substitution works")
                else:
                    print("FAIL: Variable substitution failed")
            else:
                print("FAIL: System message missing")
                
            # Check Rewriting/RAG (Mocked/Stubbed)
            # RAG context might be empty string if Chroma is down or empty.
            
        except Exception as e:
            print(f"FAIL: Request failed: {e}")

if __name__ == "__main__":
    asyncio.run(verify_prompt_process())
