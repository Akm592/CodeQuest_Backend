import asyncio
import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

URL = os.getenv("SUPABASE_URL")
KEY = os.getenv("SUPABASE_ANON_KEY")

async def create_guest():
    client: Client = create_client(URL, KEY)
    email = "guest@example.com"
    password = "guest_system_password_secure_123"
    
    print(f"Attempting to create/get user: {email}")
    
    try:
        # Try sign up
        res = client.auth.sign_up({"email": email, "password": password})
        if res.user:
            print(f"SUCCESS: Created guest user. ID: {res.user.id}")
            return res.user.id
            
        # If sign up returns User but session is None (email confirm), checking logic might differ.
        if res.user is None:
             print("Sign up returned no user. Trying sign in...")
             
    except Exception as e:
        print(f"Sign up failed (likely exists): {e}")

    try:
        res = client.auth.sign_in_with_password({"email": email, "password": password})
        if res.user:
            print(f"SUCCESS: Signed in. ID: {res.user.id}")
            return res.user.id
    except Exception as e:
        print(f"Sign in failed: {e}")
        return None

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    loop.run_until_complete(create_guest())
