// Runs only during initialization of an empty MongoDB data directory.
const username = process.env.CRAWLAB_MONGO_USERNAME;
const password = process.env.CRAWLAB_MONGO_PASSWORD;
if (!username || !password) {
  throw new Error('Missing Crawlab database credentials');
}
db.getSiblingDB('crawlab').createUser({
  user: username,
  pwd: password,
  roles: [{ role: 'readWrite', db: 'crawlab' }],
});
