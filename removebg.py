import os
from rembg import remove
from PIL import Image

input_folder = "/Users/ghanijon/Desktop/ST-Photos/orig"
output_folder = "/Users/ghanijon/Desktop/ST-Photos/edited"
background_color = (255, 255, 255)

os.makedirs(output_folder, exist_ok=True)
extensions = (".jpg", ".jpeg", ".JPG", ".JPEG", ".png", ".PNG")

for filename in os.listdir(input_folder):
    print("Found file:", filename)
    
    if filename.lower().endswith(extensions):
        input_path = os.path.join(input_folder, filename)
        output_path = os.path.join(output_folder, filename)

        try:
            with Image.open(input_path) as img:
                fg = remove(img).convert("RGBA")
                bg = Image.new("RGBA", fg.size, background_color + (255,))
                combined = Image.alpha_composite(bg, fg)
                combined.convert("RGB").save(output_path)

            print(f"Processed: {filename}")

        except Exception as e:
            print(f"Failed to process {filename}: {e}")

print("All images processed!")