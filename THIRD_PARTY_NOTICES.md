# Third party notices

The container image includes these components. Their original notices and license
terms remain in the image.

| Component | Source | License and notice location in the image |
| --- | --- | --- |
| libcsp v2.1 | [libcsp](https://github.com/libcsp/libcsp/tree/v2.1) | MIT; `/usr/share/licenses/libcsp/LICENSE` and `AUTHORS` |
| ZeroMQ (`libzmq5`, Debian bookworm package) | [Debian package](https://packages.debian.org/bookworm/libzmq5) | LGPL-3.0+ with a special exception and package-specific terms; `/usr/share/doc/libzmq5/copyright` |
| Debian base image and runtime packages | [Debian](https://www.debian.org/) | Individual package notices under `/usr/share/doc/` |

The csp-lab source and its own MIT license are at
`/usr/share/licenses/csp-lab/LICENSE` in the image. The installed package versions
can be inspected with `dpkg-query -W` inside a container.
