import os
import boto3
from dotenv import load_dotenv

load_dotenv()

def main():
    bucket = os.getenv("AWS_S3_BUCKET_NAME")
    if not bucket:
        print("S3 bucket not configured.")
        return

    s3 = boto3.client(
        's3',
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_DEFAULT_REGION", "ap-south-1")
    )
    
    company_id = os.getenv("COMPANY_ID")
    if not company_id:
        print("Set COMPANY_ID in your environment / .env")
        return
    prefix = f"documents/{company_id}/"
    
    print(f"Listing objects in s3://{bucket}/{prefix}")
    try:
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
        if 'Contents' not in resp:
            print("No files found in S3 under this company.")
            return
            
        for obj in resp['Contents']:
            key = obj['Key']
            print(f"\n{'='*60}")
            print(f"FILE: {key}")
            print(f"{'='*60}")
            
            # Download and print content
            response = s3.get_object(Bucket=bucket, Key=key)
            body = response['Body'].read()
            
            if key.endswith('.txt'):
                print(body.decode('utf-8', errors='ignore'))
            elif key.endswith('.pdf'):
                try:
                    import PyPDF2
                    import io
                    reader = PyPDF2.PdfReader(io.BytesIO(body))
                    text = ""
                    for page in reader.pages:
                        text += (page.extract_text() or "") + "\n"
                    print(text)
                except Exception as e:
                    print(f"Failed to read PDF text: {e}")
            else:
                print(f"File size: {len(body)} bytes (Raw format)")
                
    except Exception as e:
        print(f"S3 Error: {e}")

if __name__ == "__main__":
    main()
