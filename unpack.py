import os
import re

xml_file = 'app_bundle.xml'

if not os.path.exists(xml_file):
    print(f"Error: Could not find {xml_file} in current directory.")
    exit()

with open(xml_file, 'r', encoding='utf-8') as f:
    content = f.read()

# Pattern to extract path and CDATA content from <file path="...">...</file>
pattern = re.compile(r'<file\s+path=["\']([^"\']+)["\']>\s*<!\[CDATA\[(.*?)\]\]>\s*</file>', re.DOTALL)
matches = pattern.findall(content)

if not matches:
    print("No files matched the pattern. Ensure app_bundle.xml contains <file path=\"...\"> blocks.")
else:
    for file_path, file_content in matches:
        # Create directories if needed
        os.makedirs(os.path.dirname(file_path) or '.', exist_ok=True)
        
        # Write extracted content
        with open(file_path, 'w', encoding='utf-8') as out_f:
            out_f.write(file_content.strip())
        print(f"Extracted: {file_path}")

    print("\nExtraction complete!")