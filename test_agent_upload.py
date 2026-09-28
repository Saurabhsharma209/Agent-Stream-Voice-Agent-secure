import requests
import sys

def test_upload(agent_id: str, enterprise_id: str, file_path: str):
    url = f"http://localhost:5002/api/exotel-sip/agents/{agent_id}/upload-documents"
    
    headers = {
        "x-enterprise-id": enterprise_id
    }
    
    print(f"Testing upload to URL: {url}")
    print(f"Using Enterprise ID: {enterprise_id}")
    print(f"File: {file_path}")
    
    try:
        with open(file_path, 'rb') as f:
            files = {
                'files': (file_path.split("/")[-1] if "/" in file_path else file_path.split("\\")[-1], f, 'application/pdf')
            }
            response = requests.post(url, headers=headers, files=files)
            
            print(f"\nResponse Status Code: {response.status_code}")
            try:
                print(f"Response JSON: {response.json()}")
            except:
                print(f"Response Text: {response.text}")
                
    except FileNotFoundError:
        print(f"Error: Could not find the file '{file_path}'")
    except requests.exceptions.ConnectionError:
        print("Error: Could not connect to the server. Is the Python API running on port 8000?")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")

if __name__ == "__main__":
    print("--- Exotel Agent Document Upload Tester ---")
    if len(sys.argv) < 4:
        print("Usage: python test_agent_upload.py <AGENT_ID> <ENTERPRISE_ID> <FILE_PATH>")
        print("Example: python test_agent_upload.py 65b123456789abcdef012345 ent_123 dummy.pdf")
        
        # Create a dummy PDF for testing if one isn't provided
        with open("dummy_test.pdf", "w") as f:
            f.write("This is a dummy text file pretending to be a PDF for testing purposes.")
        print("\nCreated a 'dummy_test.pdf' file for you to use in testing.")
    else:
        agent_id = sys.argv[1]
        enterprise_id = sys.argv[2]
        file_path = sys.argv[3]
        test_upload(agent_id, enterprise_id, file_path)
