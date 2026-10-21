import yaml

import main

if __name__ == "__main__":
    with open("openapi.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(main.app.openapi(), f, sort_keys=False, allow_unicode=True)
    print("wrote openapi.yaml")
