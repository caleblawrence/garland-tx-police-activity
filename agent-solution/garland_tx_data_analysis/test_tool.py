from src.garland_tx_data_analysis.tools.custom_tool import FileDownloadTool
import os

def test_download():
    tool = FileDownloadTool()
    url = "https://www.google.com/robots.txt"
    save_path = "robots.txt"
    result = tool._run(url=url, save_path=save_path)
    print(result)
    
    # Verify file was downloaded
    if os.path.exists(save_path):
        print(f"Verified: '{save_path}' exists.")
        with open(save_path, 'r') as f:
            content = f.read()
            print(f"File content starts with: '{content[:50]}...'")
        os.remove(save_path)
        print(f"Cleaned up '{save_path}'.")
    else:
        print(f"Verification failed: '{save_path}' does not exist.")

if __name__ == "__main__":
    test_download()
