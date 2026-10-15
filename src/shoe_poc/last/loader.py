"""Local-only STEP input, including a narrowly scoped malformed header repair."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import tempfile


@dataclass
class LoadedLast:
    shape: object
    source: dict


class LastLoader:
    def load(self, path: str | Path) -> LoadedLast:
        from build123d import import_step
        path = Path(path).resolve()
        raw = path.read_bytes()
        text = raw.decode('utf-8', errors='replace')
        header, marker, data = text.partition('DATA;')
        if not marker:
            raise ValueError('STEP DATA section is missing')
        match = re.search(r'FILE_NAME\s*\((.*?)\);', header, flags=re.S)
        original_header = match.group(0) if match else None
        repaired = bool(match and not match.group(1).lstrip().startswith("'"))
        if repaired:
            # Only change the malformed metadata record; retain DATA verbatim.
            header = header[:match.start()] + "FILE_NAME('local-input','',(''),(''),'', '', '');" + header[match.end():]
            with tempfile.TemporaryDirectory(prefix='shoe-step-') as directory:
                prepared = Path(directory) / 'last.step'
                prepared.write_text(header + marker + data)
                shape = import_step(prepared)
        else:
            shape = import_step(path)
        unit_tokens = re.findall(r'SI_UNIT\s*\(\s*(\.[A-Z]+\.|\$)\s*,\s*\.METRE\.\s*\)', text)
        conversion_units = re.findall(r"CONVERSION_BASED_UNIT\s*\(\s*'([^']+)'", text)
        if not unit_tokens and not conversion_units:
            raise ValueError('STEP length units could not be established')
        if len(shape.solids()) != 1:
            raise ValueError(f'Expected one shoe-last solid, found {len(shape.solids())}')
        if not shape.is_valid:
            raise ValueError('Imported shoe last has invalid B-Rep topology')
        return LoadedLast(shape, {
            'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
            'size_bytes': len(raw), 'header_repaired_in_temporary_copy': repaired,
            'original_file_name_record': original_header,
            'source_length_unit_prefixes': unit_tokens,
            'source_conversion_units': conversion_units,
            'working_unit': 'mm', 'units_note': 'OpenCascade STEP reader converts input length units to millimetres.',
            'license_status': 'REQUIRES_PURCHASE_AUTHORIZATION_REVIEW',
            'license_notice': original_header if original_header and 'License' in original_header else 'Source/license to be confirmed',
            'local_only': True,
        })
