import arrow
import json
import boto3
import os
from decimal import Decimal

TABLE_NAME = os.environ.get('TABLE_NAME', '')
REGION_NAME = os.environ.get('REGION_NAME', 'us-east-1')
LAMBDA_FUNC_REGION_NAME = os.environ.get('LAMBDA_FUNC_REGION_NAME', 'us-east-1')
STEP_FUNC_REGION_NAME = os.environ.get('STEP_FUNC_REGION_NAME', 'us-east-1')

dynamodb = boto3.resource('dynamodb', region_name=REGION_NAME)
table = dynamodb.Table(TABLE_NAME)


def to_json(value):
    """Serializes job inputs; DynamoDB returns numbers as Decimal."""
    def default(obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")
    return json.dumps(value, default=default)

def load_jobs():
    """Loads the jobs from the DynamoDB Table."""
    jobs = []
    response = table.scan()
    jobs.extend(response['Items'])
    while 'LastEvaluatedKey' in response:
        response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
        jobs.extend(response['Items'])
    return jobs

def check_not_run_today(job, now):
    """Checks the job hasn't already run today, so it runs at most once a day."""
    last_run_time_raw = job.get('last_run_time')

    if not last_run_time_raw:
        return True

    last_run_date = arrow.get(last_run_time_raw).to('Asia/Kolkata').date()
    return last_run_date < now.date()

def check_date_to_run(job, now):
    """Checks if the job should run on the current date."""

    interval_args = job.get('interval_args')
    start_date = arrow.get(interval_args['start_date']).date()
    end_date = arrow.get(interval_args['end_date']).date()

    today = now.date()
    if start_date <= today <= end_date:
        return True
    return False

def update_last_run_time(job_name, now):
    """Updates the last run time for jobs."""
    table.update_item(
        Key={'job_name': job_name},
        UpdateExpression='SET last_run_time = :t',
        ExpressionAttributeValues={':t': now.isoformat()}
    )

def trigger_step_functions(job, now):
    """Triggers the step function."""
    client = boto3.client('stepfunctions', region_name=STEP_FUNC_REGION_NAME)
    client.start_execution(
        stateMachineArn=job['target_arn'],
        name=f"{job['job_name']}-{now.format('YYYYMMDD-HHmmss')}",
        input=to_json(job.get('inputs', {}))
    )

def triggers_lambda_function(job):
    """Triggers the lambda function."""
    client = boto3.client('lambda', region_name=LAMBDA_FUNC_REGION_NAME)
    client.invoke(
        FunctionName=job['target_arn'],
        InvocationType='Event',
        Payload=to_json(job.get('inputs', {}))
    )

def run_jobs(jobs, now):
    """Run eligible jobs."""
    for job in jobs:
        if not job.get('is_enabled', True):
            continue

        # One bad job shouldn't stop the remaining jobs from running.
        try:
            if (
                not check_date_to_run(job, now) or
                not check_not_run_today(job, now)
            ):
                continue

            run_type = job.get('run_type')

            if run_type == 'step_function':
                trigger_step_functions(job, now)
            elif run_type == 'lambda_function':
                triggers_lambda_function(job)
            else:
                print(f"Skipping job {job.get('job_name')}: unknown run_type {run_type!r}")
                continue

            update_last_run_time(job.get('job_name'), now)
            print(f"Triggered job {job.get('job_name')}")
        except Exception as e:
            print(f"Failed to run job {job.get('job_name')}: {e!r}")

def schedule_jobs():
    """Entry point to schedule eligible jobs."""
    now = arrow.now('Asia/Kolkata')
    jobs = load_jobs()
    run_jobs(jobs, now)

def lambda_handler(event, context):
    """Main Entry Point for the lambda function."""
    schedule_jobs()
