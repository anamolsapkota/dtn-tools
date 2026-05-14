# Contributing to dtn-tools

Thank you for your interest in contributing to dtn-tools! This project aims to make DTN (Delay-Tolerant Networking) accessible to everyone.

## How to Contribute

### Reporting Issues

- Use [GitHub Issues](https://github.com/anamolsapkota/dtn-tools/issues) to report bugs or request features
- Include your ION-DTN version, OS, and Python version
- Provide relevant log output (`dtn logs` or `journalctl -u dtn-discovery`)

### Submitting Changes

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Make your changes
4. Test on a real DTN node if possible
5. Submit a pull request with a clear description

### Code Style

- Python: Follow PEP 8
- Shell scripts: Use `set -e` and quote variables
- ION configs: Comment each section

### Testing

Test your changes on a DTN node with ION-DTN installed:

```bash
# Copy to a DTN node
scp -r . user@dtn-node:~/dtn-tools/

# Test the CLI
ssh user@dtn-node "cd dtn-tools && python3 dtn status"
```

## Areas for Contribution

### High Priority

- **Docker support** — Containerized DTN node for easy testing
- **Automated tests** — Unit tests for discovery and config generation
- **uD3TN support** — Extend the CLI to work with uD3TN in addition to ION-DTN
- **Web dashboard** — Browser-based node monitoring interface

### Medium Priority

- **TCP CLA support** — Add TCP convergence layer in addition to UDP
- **Config validation** — Validate ION configs before applying
- **Migration tool** — Upgrade ION configs between versions
- **macOS support** — ION-DTN on macOS via Homebrew

### Research Opportunities

- **Performance benchmarking** — Measure discovery latency and bundle throughput
- **Multi-hop routing analysis** — Study CGR behavior with auto-discovered contacts
- **DTN topology mapping** — Visualize the global DTN network from discovery data
- **Comparison with other implementations** — Benchmark against uD3TN, DTN7, HDTN

## Development Setup

```bash
git clone https://github.com/anamolsapkota/dtn-tools.git
cd dtn-tools

# Install ION-DTN (see https://ion-dtn.readthedocs.io/)
# Install Python dependencies
pip install requests

# Run locally
python3 dtn status
```

## Contact

- Open an issue for questions
- Email: anamol@ekrasunya.com
