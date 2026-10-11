declare module "*.css";
declare module "*.svg" {
  const url: string;
  export default url;
}
declare module "*.html" {
  const page: import("bun").HTMLBundle;
  export default page;
}
