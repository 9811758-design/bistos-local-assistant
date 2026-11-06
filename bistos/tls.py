import ipaddress
import sys
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from bistos.settings import Settings


def main() -> None:
    address = ipaddress.ip_address(sys.argv[1])
    directory = Settings.load().data_dir / "tls"
    directory.mkdir(parents=True, exist_ok=True)
    certificate_path = directory / "server.crt"
    key_path = directory / "server.key"
    if certificate_path.is_file() and key_path.is_file():
        cert = x509.load_pem_x509_certificate(certificate_path.read_bytes())
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        if address in names.get_values_for_type(
            x509.IPAddress
        ) and cert.not_valid_after_utc > datetime.now(UTC):
            return
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Bistos local assistant")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    x509.IPAddress(address),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    certificate_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


if __name__ == "__main__":
    main()
