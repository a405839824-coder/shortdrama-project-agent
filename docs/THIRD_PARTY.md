# Third-party dependencies

The source distribution does not vendor Python dependencies or include `.venv`.
Install dependencies from `requirements.txt`. Their licenses remain separate
from the MIT license covering this project's original code and documentation.

Direct runtime dependency metadata inspected in the local verification
environment on 2026-10-26:

| Package | Requirement | Inspected version | Declared license |
| --- | --- | --- | --- |
| mcp | ==2.0.0 | 2.0.0 | MIT |
| openpyxl | ==3.1.5 | 3.1.5 | MIT |
| pypdf | ==6.0.0 | 6.0.0 | BSD-3-Clause |
| PyJWT[crypto] | ==2.10.1 | 2.10.1 | MIT |
| filelock | ==3.18.0 | 3.18.0 | Unlicense |
| jsonschema | >=4.20.0,<5 | 4.26.0 | MIT |

Cryptography and the other transitive dependencies retain the license files
and notices in their distributions. This table describes the direct package
metadata, not a legal opinion or a complete inventory of licenses in a built
container. Preserve all dependency notices if redistributing installed Python
packages or a Docker image. The local verification evidence includes the
resolved environment inventory separately from the source ZIP.

The repository also contains historical copies of this project's validators
for reading older data. Those files remain part of this project's source;
their original compatibility fingerprints and behavior are retained.

References:

- [Python SDK for MCP](https://github.com/modelcontextprotocol/python-sdk)
- [openpyxl](https://pypi.org/project/openpyxl/)
- [pypdf](https://github.com/py-pdf/pypdf)
- [PyJWT](https://github.com/jpadilla/pyjwt)
- [filelock](https://github.com/tox-dev/filelock)
- [jsonschema](https://github.com/python-jsonschema/jsonschema)
