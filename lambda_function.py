import arrow
import json
import boto3
import os

TABLE_NAME = os.environ.get('TABLE_NAME', '')
REGION_NAME = os.environ.get('REGION_NAME', 'us-east-1')
LAMBDA_FUNC_REGION_NAME = os.environ.get('LAMBDA_FUNC_REGION_NAME', 'us-east-1')
STEP_FUNC_REGION_NAME = os.environ.get('STEP_FUNC_REGION_NAME', 'us-east-1')

dynamodb = boto3.resource('dynamodb', region_name=REGION_NAME)
table = dynamodb.Table(TABLE_NAME)


def load_jobs():
    """Loads the jobs from the DynamoDB Table."""
    jobs = []
    response = table.scan()
    jobs.extend(response['Items'])
    return jobs

def check_time_to_run(job, now):
    """Checks job run eligibility based on time."""
    last_run_time_raw = job.get('last_run_time')

    if last_run_time_raw:
        last_check = arrow.get(last_run_time_raw)
    else:
        last_check = now.shift(minutes=-5)

    today_str = now.format('YYYY-MM-DD')
    run_times = job.get('run_times', [])
    for run_time in run_times:

        scheduled_dt = arrow.get(f"{today_str} {run_time}", 'YYYY-MM-DD HH:mm', tzinfo='Asia/Kolkata')
        if last_check <= scheduled_dt <= now:
            return True
    return False

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
        input=json.dumps(job.get('inputs', {}))
    )

def triggers_lambda_function(job):
    """Triggers the lambda function."""
    client = boto3.client('lambda', region_name=LAMBDA_FUNC_REGION_NAME)
    client.invoke(
        FunctionName=job['target_arn'],
        InvocationType='Event',
        Payload=json.dumps(job.get('inputs', {}))
    )

def run_jobs(jobs, now):
    """Run eligible jobs."""
    for job in jobs:        
        if not job.get('is_enabled', True):
            continue
        
        if (
            not check_date_to_run(job, now) or
            not check_time_to_run(job, now)
        ):
            continue

        run_type = job.get('run_type')

        if run_type == 'step_function':
            trigger_step_functions(job, now)
        elif run_type == 'lambda_function':
            triggers_lambda_function(job)

        update_last_run_time(job.get('job_name'), now)

def schedule_jobs():
    """Main entry point to schedule eligible jobs."""
    now = arrow.now('Asia/Kolkata')
    jobs = load_jobs()
    run_jobs(jobs, now)
