import yaml


def main():
    with open("openapi.yaml", encoding="utf-8") as f:
        spec = yaml.safe_load(f)
    print(f"{spec['info']['title']} v{spec['info']['version']}\n")
    for path, methods in spec["paths"].items():
        for method, op in methods.items():
            print(f"{method.upper():5} {path}  - {op.get('summary', '')}")
            for p in op.get("parameters", []):
                req = "required" if p.get("required") else "optional"
                print(f"        in:{p['in']:6} {p['name']} ({req})")
            if "requestBody" in op:
                ref = op["requestBody"]["content"]["application/json"]["schema"]["$ref"].split("/")[-1]
                print(f"        body: {ref}")
            for status, resp in op["responses"].items():
                content = resp.get("content", {})
                schema = ""
                if content:
                    s = next(iter(content.values()))["schema"]
                    schema = s.get("$ref", str(s.get("type"))).split("/")[-1]
                hdrs = ",".join(resp.get("headers", {}))
                print(f"        {status}: {resp['description'][:58]:58} {schema:22} {('hdr:' + hdrs) if hdrs else ''}")
            print()


if __name__ == "__main__":
    main()
