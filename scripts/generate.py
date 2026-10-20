import subprocess
import sys
from pathlib import Path
import yaml
from openapi_spec_validator import validate

root = Path(__file__).resolve().parents[1]
contract = root / 'openapi/marketplace.yaml'
spec = yaml.safe_load(contract.read_text())
validate(spec)
output = root / 'app/generated'
output.mkdir(exist_ok=True)
subprocess.run([
    sys.executable, '-m', 'datamodel_code_generator',
    '--input', str(contract), '--input-file-type', 'openapi',
    '--output', str(output / 'models.py'),
    '--output-model-type', 'pydantic_v2.BaseModel',
    '--target-python-version', '3.12', '--field-constraints',
    '--strict-nullable', '--disable-timestamp',
    '--base-class', 'app.schema_base.ContractModel',
], check=True)
model_path = output / 'models.py'
model_path.write_text('\n'.join(line for line in model_path.read_text().splitlines() if not line.lstrip().startswith('#')) + '\n')
(output / '__init__.py').write_text('')
lines = [
    'from typing import Optional', 'from uuid import UUID',
    'from fastapi import APIRouter, Depends, Query, Response',
    'from sqlalchemy.orm import Session',
    'from app.generated import models', 'from app import operations',
    'from app.db import get_db', 'from app.security import require_roles',
    'from app.validation import validate_contract',
    '', 'router = APIRouter()', '',
]
for path, item in spec['paths'].items():
    for method, operation in item.items():
        name = operation['operationId']
        success = next((code, data) for code, data in operation['responses'].items() if code.startswith('2'))
        code, response = success
        response_type = 'None' if code == '204' else 'models.' + response['content']['application/json']['schema']['$ref'].split('/')[-1]
        args = []
        call = []
        for param in operation.get('parameters', []):
            field = param['name']
            schema = param['schema']
            type_name = 'models.' + schema['$ref'].split('/')[-1] if '$ref' in schema else ('UUID' if schema.get('format') == 'uuid' else {'integer':'int','string':'str'}[schema['type']])
            if param['in'] == 'path':
                args.append(f'{field}: {type_name}')
            else:
                default = schema.get('default')
                if default is None:
                    type_name = f'Optional[{type_name}]'
                options = [repr(default)]
                for source, target in [('minimum','ge'),('maximum','le'),('minLength','min_length'),('maxLength','max_length')]:
                    if source in schema:
                        options.append(f'{target}={schema[source]!r}')
                args.append(f'{field}: {type_name} = Query({", ".join(options)})')
            call.append(f'{field}={field}')
        if 'requestBody' in operation:
            body_type = operation['requestBody']['content']['application/json']['schema']['$ref'].split('/')[-1]
            args.insert(0, f'body: models.{body_type}')
            call.append('body=body')
        args.append('db: Session = Depends(get_db)')
        call.append('db=db')
        if operation.get('security') != []:
            roles = ', '.join(repr(role) for role in operation['x-roles'])
            args.append(f'actor=Depends(require_roles({roles}))')
            call.append('actor=actor')
        lines.append(f'@router.{method}({path!r}, response_model={response_type}, status_code={code}, dependencies=[Depends(validate_contract)])')
        lines.append(f'def {name}({", ".join(args)}):')
        if code == '204':
            lines.append(f'    operations.{name}({", ".join(call)})')
            lines.append('    return Response(status_code=204)')
        else:
            lines.append(f'    return operations.{name}({", ".join(call)})')
        lines.append('')
(output / 'routes.py').write_text('\n'.join(lines))
print('Generated app/generated/models.py and app/generated/routes.py')
