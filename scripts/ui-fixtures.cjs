// Synthetic media fixtures use the same work envelope as the combined history API.
function mediaTasks(jobs, requestUrl) {
  const url = new URL(requestUrl);
  return jobs.map(job => ({
    source: 'media', id: job.id, title: job.title, kind: job.kind,
    service: job.service, label: job.kind, status: job.status,
    owner_id: job.owner_id, origin: job.origin || 'web',
    executor: job.worker_id || '배정 대기', created_at: job.created_at, data: job,
  })).filter(task => (!url.searchParams.has('status') || task.status === url.searchParams.get('status')) &&
    (!url.searchParams.has('service') || task.service === url.searchParams.get('service')));
}
module.exports = { mediaTasks };
