# Fleet plugin release check

## Source and local package verification

The five packages were copied from the tested, committed plugin sources. The marketplace suite passed: **38 passed**. Each local artifact was packaged deterministically, had one package root, and its embedded manifest matched the source manifest.

| Plugin | Version | Source commit | Local artifact SHA-256 |
| --- | --- | --- | --- |
| Playbooks | 0.57.16 | `8160da9` | `2fea074f9124958f078664ca66b9562f1cdd81844c62ce8a8214ab07d6d634a3` |
| Scheduler | 0.8.3 | `d63c8c3` | `81504526abb04f588994e554e1420d48617b32c7bc396090715b1b564e5a93b4` |
| DB | 0.1.1 | `d8644cc` | `c424ac0f08cd96acf6af54ba34f46e005f6aa056e710e04783a76c8a5d4aef2f` |
| MCP | 0.2.1 | `2e872a1` | `f9abc5c74f6688af1272d806b8e92884e1a43ea668551fce2d940dc2c990b270` |
| Web Access | 0.3.1 | `bf1b4b1` | `0d2ed0f430f775ddbcc8032c1ef5d6b7537fc1230fcedd541f3ab7ef9dac459d` |

Before release, the live official index contained 35 plugins and versions Playbooks 0.57.2, Scheduler 0.8.0, DB 0.1.0, MCP 0.2.0, and Web Access 0.3.0. The post-deploy catalog and artifact verification will be appended after deployment.
