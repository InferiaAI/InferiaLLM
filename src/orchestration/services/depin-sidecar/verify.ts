import { validate } from '@nosana/authorization';
import bs58 from 'bs58';

const ownerAddress = "3hrM8kAe924emh2JWDh11PMD7rc1krJagsjTY4mHobyV";
const signature = "5vGYhudKHyuZxaTCYjpyNuLMNGsUMEHYStXCFeCNkpNa1XQAtsbNk8nZb4S6sNKaaYfxGEL3wW4NhYEg8wWpYWJt";

const pubKey = bs58.decode(ownerAddress);

console.log("Valid for 'Hello Nosana Node!' ?");
try {
  const isValid = validate("Hello Nosana Node!:" + signature, pubKey);
  console.log("Result:", isValid);
} catch (e: any) { console.log(e.message); }

console.log("\nValid for empty string?");
try {
  const isValid2 = validate(":" + signature, pubKey);
  console.log("Result:", isValid2);
} catch (e: any) { console.log(e.message); }
