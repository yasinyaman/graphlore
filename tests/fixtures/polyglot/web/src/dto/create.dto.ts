export class CreateDto {
  constructor(public name: string) {}

  describe(): string {
    return `item ${this.name}`;
  }
}
